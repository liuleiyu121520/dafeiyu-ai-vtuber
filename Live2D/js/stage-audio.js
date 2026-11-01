/* 舞台音频播放器：把主程序合成的音频在浏览器里真播出来，并用真实振幅驱动口型。
 *
 * 为什么是浏览器播放
 * ------------------
 * Docker Desktop 的 Linux 容器没有声卡（/dev/snd 不暴露），pygame 播不出声音。
 * 主程序把音频字节交给 5500 的舞台服务，这里用 Web Audio 解码播放；
 * OBS 浏览器源直接采到这个声音，不需要 BlackHole，也不需要往容器里映射声卡。
 *
 * 口型为什么是真的
 * ----------------
 * AnalyserNode.getFloatTimeDomainData 读的是**正在播放的那段 PCM** 的时域波形，
 * 算 RMS 后映射成 0~1 的开口度写进 window.__songroomLipSync。
 * bundle.songroom-lipsync.js 每帧把它 setParameterValueByIndex(ParamMouthOpenY)。
 * 全程没有随机数、没有定时器假动。
 *
 * 通道语义与主程序一致：0=弹幕回复 1=文案 2=BGM 3=点歌
 */
(() => {
  'use strict';

  const STAGE_BASE = (() => {
    // 允许用 ?stage=host:port 覆盖（OBS 在另一台机器上取画面时用）
    const override = new URLSearchParams(location.search).get('stage');
    if (override) return override.startsWith('http') ? override : 'http://' + override;
    return 'http://127.0.0.1:5500';
  })();

  // 关掉音频（只做画面）时用 ?audio=0
  const AUDIO_ENABLED = new URLSearchParams(location.search).get('audio') !== '0';

  const CHANNELS = [0, 1, 2, 3];
  const CHANNEL_NAMES = { 0: 'comment', 1: 'copywriting', 2: 'bgm', 3: 'song' };

  // 口型映射参数：和原 songroom-lipsync.js 的曲线保持一致，听感才不会变
  const LIP_NOISE_FLOOR = 0.012;
  const LIP_GAIN = 2.8;
  const LIP_CURVE = 0.68;
  // 开口要快（说话立刻张嘴），收口要慢一点（不然一卡一卡）
  const LIP_ATTACK = 24;
  const LIP_RELEASE = 15;

  const SYNC_INTERVAL_MS = 120;   // 主程序侧命令的拉取节奏
  const REPORT_INTERVAL_MS = 120; // 播放状态回报节奏

  let ctx = null;
  let masterGain = null;
  let analyser = null;
  let analyserData = null;

  // channel -> 播放单元
  const units = {};
  // channel -> 最近一次从服务端看到的 seq
  const lastSeq = {};
  // channel -> 最近一次上报的 {state, positionMs}
  const lastReport = {};

  let lipTarget = 0;
  let lipCurrent = 0;
  let lipSourceAt = 0;
  let lastFrame = performance.now();

  const status = {
    ready: false,
    context: 'suspended',
    channels: {},
    lastError: '',
    served: 0,
  };
  window.__stageAudio = status;
  window.__stageAudioReady = false;

  function log(...args) {
    if (window.__stageAudioDebug) console.log('[stage-audio]', ...args);
  }

  // ------------------------------------------------------------ Web Audio

  function ensureContext() {
    if (ctx) return ctx;
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) {
      status.lastError = '浏览器不支持 Web Audio';
      return null;
    }
    ctx = new Ctor({ latencyHint: 'interactive' });
    masterGain = ctx.createGain();
    masterGain.gain.value = 1.0;
    analyser = ctx.createAnalyser();
    // 2048 点 @48kHz ≈ 42ms 窗口：够平滑，又不会明显滞后
    analyser.fftSize = 2048;
    analyser.smoothingTimeConstant = 0.0;
    analyserData = new Float32Array(analyser.fftSize);
    // 所有通道 → master → analyser → 扬声器
    // analyser 串在链路上，所以读到的就是真正在响的混合信号
    masterGain.connect(analyser);
    analyser.connect(ctx.destination);
    status.ready = true;
    return ctx;
  }

  /* 浏览器自动播放策略：AudioContext 初始是 suspended，
   * 必须有用户手势才能 resume。OBS 浏览器源默认允许自动播放，
   * 但 Chrome 里调试时必须点一下页面。这里两手准备：
   *   1. 主动尝试 resume
   *   2. 监听首次点击/按键/触摸再 resume
   */
  function unlock() {
    const context = ensureContext();
    if (!context) return Promise.resolve(false);
    if (context.state === 'running') {
      status.context = 'running';
      window.__stageAudioReady = true;
      return Promise.resolve(true);
    }
    return context.resume().then(() => {
      status.context = context.state;
      window.__stageAudioReady = context.state === 'running';
      log('audio context', context.state);
      return window.__stageAudioReady;
    }).catch((error) => {
      status.lastError = String(error);
      return false;
    });
  }

  ['click', 'keydown', 'touchstart', 'pointerdown'].forEach((name) => {
    addEventListener(name, unlock, { passive: true });
  });

  // ------------------------------------------------------------ 播放单元

  function makeUnit(channel) {
    return {
      channel,
      buffer: null,
      source: null,
      gain: null,
      seq: -1,
      fileId: '',
      state: 'idle',
      startedAt: 0,
      offset: 0,
      error: '',
    };
  }

  CHANNELS.forEach((channel) => {
    units[channel] = makeUnit(channel);
    lastSeq[channel] = 0;
    lastReport[channel] = { state: 'idle', positionMs: 0 };
  });

  function stopUnit(unit, reason) {
    if (unit.source) {
      try { unit.source.onended = null; unit.source.stop(); } catch (e) { /* 已经停了 */ }
      unit.source = null;
    }
    unit.buffer = null;
    unit.state = 'idle';
    unit.startedAt = 0;
    unit.offset = 0;
    if (reason) log('channel', unit.channel, 'stop:', reason);
  }

  async function loadBuffer(fileId) {
    const url = STAGE_BASE + '/audio/file/' + encodeURIComponent(fileId);
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) throw new Error('audio fetch ' + response.status);
    const bytes = await response.arrayBuffer();
    status.served += 1;
    // decodeAudioData 会消耗掉 ArrayBuffer，传副本避免重试时数据已被分离
    return await ctx.decodeAudioData(bytes.slice(0));
  }

  async function playUnit(unit, command) {
    const context = ensureContext();
    if (!context) return;

    stopUnit(unit, 'restart');

    try {
      const buffer = await loadBuffer(command.fileId);
      // 加载是异步的：回来时这一段可能已经被新的 play 顶掉了
      if (lastSeq[unit.channel] !== command.seq) {
        log('channel', unit.channel, 'stale load dropped');
        return;
      }

      unit.buffer = buffer;
      unit.fileId = command.fileId;
      unit.seq = command.seq;
      unit.offset = 0;

      const gain = context.createGain();
      const volume = Number(command.volume);
      gain.gain.value = Number.isFinite(volume) ? Math.max(0, Math.min(1, volume)) : 1;
      gain.connect(masterGain);

      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(gain);

      source.onended = () => {
        // stop() 主动停也会触发 onended，要区分开
        if (unit.source !== source) return;
        unit.source = null;
        unit.state = 'ended';
        unit.startedAt = 0;
        log('channel', unit.channel, 'ended');
      };

      unit.source = source;
      unit.gain = gain;
      unit.state = 'playing';
      unit.startedAt = context.currentTime;
      source.start(0);
      log('channel', unit.channel, 'playing', buffer.duration.toFixed(2) + 's');
    } catch (error) {
      unit.state = 'error';
      unit.error = String(error).slice(0, 200);
      status.lastError = unit.error;
      console.error('[stage-audio] 播放失败', error);
    }
  }

  // ------------------------------------------------------------ 服务端命令

  function applySnapshot(snapshot) {
    if (!snapshot || !snapshot.channels) return;
    CHANNELS.forEach((channel) => {
      const remote = snapshot.channels[String(channel)];
      if (!remote) return;
      const unit = units[channel];
      const seq = Number(remote.seq) || 0;
      if (seq === lastSeq[channel]) return;
      lastSeq[channel] = seq;

      if (remote.state === 'error') {
        unit.state = 'error';
        unit.error = remote.error || '';
        return;
      }

      if (remote.fileId && remote.url) {
        playUnit(unit, {
          seq,
          fileId: remote.fileId,
          volume: remote.volume,
        });
        return;
      }

      // 没有 fileId 的新 seq = 停止/淡出
      if (remote.state === 'stopped' || remote.state === 'fading') {
        const fadeMs = 220;
        if (unit.gain && ctx) {
          try {
            unit.gain.gain.setTargetAtTime(0, ctx.currentTime, fadeMs / 1000 / 3);
          } catch (e) { /* 忽略 */ }
        }
        const source = unit.source;
        setTimeout(() => {
          if (unit.source === source) stopUnit(unit, 'server stop');
        }, fadeMs);
        unit.state = 'stopped';
      }
    });
  }

  async function pollCommands() {
    if (!AUDIO_ENABLED) {
      setTimeout(pollCommands, 1000);
      return;
    }
    try {
      const response = await fetch(STAGE_BASE + '/audio/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ commands: [], volumes: {} }),
        cache: 'no-store',
        signal: AbortSignal.timeout(2000),
      });
      if (response.ok) applySnapshot(await response.json());
    } catch (error) {
      // 舞台服务不在也不该影响角色渲染
      status.lastError = String(error).slice(0, 120);
    } finally {
      setTimeout(pollCommands, SYNC_INTERVAL_MS);
    }
  }

  // ------------------------------------------------------------ 状态回报

  function channelState(unit) {
    if (!ctx) return 'idle';
    if (unit.state === 'playing' && unit.source) return 'playing';
    if (unit.state === 'paused') return 'paused';
    if (unit.state === 'error') return 'error';
    if (unit.state === 'ended') return 'ended';
    return 'idle';
  }

  function positionMs(unit) {
    if (!ctx || !unit.source || unit.state !== 'playing') return 0;
    return Math.max(0, Math.round((ctx.currentTime - unit.startedAt) * 1000));
  }

  async function reportState() {
    if (!AUDIO_ENABLED) {
      setTimeout(reportState, 1000);
      return;
    }
    // 只报「变化」的通道 + 每 1 秒补一次心跳，减少无意义请求
    const now = performance.now();
    const heartbeat = !reportState.lastHeartbeat || now - reportState.lastHeartbeat > 1000;
    const reports = [];
    CHANNELS.forEach((channel) => {
      const unit = units[channel];
      const state = channelState(unit);
      const ms = positionMs(unit);
      const previous = lastReport[channel];
      if (state === previous.state && Math.abs(ms - previous.positionMs) < 400 && !heartbeat) return;
      lastReport[channel] = { state, positionMs: ms };
      reports.push({ channel, state, seq: unit.seq, positionMs: ms, error: unit.error });
    });
    if (heartbeat) reportState.lastHeartbeat = now;
    if (!reports.length) {
      setTimeout(reportState, REPORT_INTERVAL_MS);
      return;
    }
    try {
      await fetch(STAGE_BASE + '/audio/report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(reports[0]),
        cache: 'no-store',
        signal: AbortSignal.timeout(1500),
      });
    } catch (error) {
      // 同上，忽略
    } finally {
      setTimeout(reportState, REPORT_INTERVAL_MS);
    }
  }

  // ------------------------------------------------------------ 口型

  function measureLevel() {
    if (!analyser) return 0;
    analyser.getFloatTimeDomainData(analyserData);
    let sum = 0;
    for (let i = 0; i < analyserData.length; i += 1) {
      const sample = analyserData[i];
      sum += sample * sample;
    }
    const rms = Math.sqrt(sum / analyserData.length);
    return Math.max(0, Math.min(1, rms));
  }

  function lipLoop(now) {
    // 只有在真的有音频在播时才读分析器，否则口型必须归零
    const anyPlaying = CHANNELS.some((channel) => {
      const unit = units[channel];
      return unit.state === 'playing' && unit.source && channel !== 2; // BGM 不驱动口型
    });

    if (anyPlaying) {
      const rms = measureLevel();
      const scaled = Math.min(1, Math.pow(Math.max(0, rms - LIP_NOISE_FLOOR) * LIP_GAIN, LIP_CURVE));
      lipTarget = scaled;
      lipSourceAt = now;
    } else {
      lipTarget = 0;
    }

    const dt = Math.min(0.1, (now - lastFrame) / 1000);
    lastFrame = now;
    const rate = lipTarget > lipCurrent ? LIP_ATTACK : LIP_RELEASE;
    lipCurrent += (lipTarget - lipCurrent) * Math.min(1, dt * rate);
    if (lipCurrent < 0.003) lipCurrent = 0;

    // 与 bundle.songroom-lipsync.js 约定完全一致：0~1 的开口度
    window.__songroomLipSync = Math.max(0, Math.min(1, lipCurrent));
    window.__songroomLipStatus = {
      ready: anyPlaying,
      sourcePid: anyPlaying ? 1 : 0,
      sessionCount: anyPlaying ? 1 : 0,
      raw: lipCurrent,
    };

    CHANNELS.forEach((channel) => {
      const unit = units[channel];
      status.channels[channel] = {
        name: CHANNEL_NAMES[channel],
        state: channelState(unit),
        seq: unit.seq,
      };
    });
    status.context = ctx ? ctx.state : 'none';
    status.lip = Math.round(lipCurrent * 1000) / 1000;

    requestAnimationFrame(lipLoop);
  }

  // ------------------------------------------------------------ 启动

  ensureContext();
  // 先尝试一次自动解锁（OBS 里通常直接成功），失败就等用户手势
  unlock();
  requestAnimationFrame(lipLoop);
  pollCommands();
  reportState();

  // 页面被 OBS 刷新/切场景后，Web Audio 可能被挂起，重新解锁
  addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && ctx && ctx.state !== 'running') unlock();
  });
})();
