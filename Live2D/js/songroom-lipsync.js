(() => {
  /* 口型信号有两个来源，二选一：
   *
   * 1. 浏览器音频桥（Docker / OBS 浏览器源）—— 由 stage-audio.js 提供。
   *    它读的是真正在播放的 PCM 振幅，window.__stageAudio 存在就说明这个模式生效。
   *
   * 2. Windows 核心播放会话（原方案）—— 由 tools/songroom_lipsync.py 轮询
   *    Windows CoreAudio 的 IAudioMeterInformation，写到 out/songroom/mouth.json。
   *    只有在没有浏览器音频桥时才会走这条（Windows 直装场景）。
   *
   * 两条路的输出约定完全一样：window.__songroomLipSync 是 0~1 的开口度。
   */
  const browserAudio = typeof window.__stageAudio !== 'undefined';
  if (browserAudio) {
    // 浏览器音频桥自己驱动 __songroomLipSync，这里只补状态位，绝不覆盖它
    window.__songroomLipStatus = window.__songroomLipStatus || {
      ready: false, sourcePid: 0, sessionCount: 0, raw: 0,
    };
    return;
  }

  let target=0,current=0,sourceTime=0,lastFrame=performance.now();
  window.__songroomLipSync=0;
  window.__songroomLipStatus={ready:false,sourcePid:0,sessionCount:0,raw:0};
  async function poll(){
    try{
      const response=await fetch('../out/songroom/mouth.json?_='+Date.now(),{cache:'no-store',signal:AbortSignal.timeout(1000)});
      if(!response.ok)throw new Error('mouth signal unavailable');
      const data=await response.json();
      const fresh=Date.now()/1000-Number(data.updated_at)<1.5;
      const raw=Math.max(0,Math.min(1,Number(data.peak)||0));
      const ready=fresh&&Number(data.source_pid)>0&&Number(data.session_count)>0;
      sourceTime=ready?Number(data.updated_at):0;
      target=ready?Math.min(1,Math.pow(Math.max(0,raw-.012)*2.8,.68)):0;
      window.__songroomLipStatus={ready,sourcePid:Number(data.source_pid)||0,sessionCount:Number(data.session_count)||0,raw};
    }catch{
      target=0;sourceTime=0;window.__songroomLipStatus.ready=false;
    }finally{setTimeout(poll,65);}
  }
  function tick(now){
    if(Date.now()/1000-sourceTime>1.5)target=0;
    const dt=Math.min(.1,(now-lastFrame)/1000);lastFrame=now;
    current+=(target-current)*Math.min(1,dt*(target>current?24:15));
    if(current<.003)current=0;
    window.__songroomLipSync=Math.max(0,Math.min(1,current));
    requestAnimationFrame(tick);
  }
  poll();requestAnimationFrame(tick);
})();
