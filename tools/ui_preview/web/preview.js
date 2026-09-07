'use strict';
const screen = document.querySelector('#screen');
const status = document.querySelector('#status');
let session, frame = -1, currentUrl, sending = Promise.resolve();
const pending = [];
function send(event) {
  if (!session) return;
  if (pending.length >= 200) return;
  pending.push(event);
  sending = sending.then(async () => {
    const body = pending.shift();
    if (!body) return;
    try {
      const result = await fetch('/input', {method:'POST', headers:{'Content-Type':'application/json','X-Preview-Token':session.token}, body:JSON.stringify(body)});
      if (!result.ok) status.textContent = '輸入未送達，請稍後重試';
    } catch { status.textContent = '連線中斷，正在重新連線…'; }
  });
}
function pointer(event, action) {
  if (!session) return;
  const rect = screen.getBoundingClientRect();
  send({type:'pointer', action, x:(event.clientX-rect.left)/rect.width*session.width, y:(event.clientY-rect.top)/rect.height*session.height});
}
screen.addEventListener('pointerdown', e => {screen.focus();screen.setPointerCapture(e.pointerId);pointer(e,'down');e.preventDefault();});
screen.addEventListener('pointermove', e => {if (e.buttons) pointer(e,'move');});
screen.addEventListener('pointerup', e => pointer(e,'up'));
screen.addEventListener('pointercancel', e => pointer(e,'up'));
screen.addEventListener('wheel', e => {pointer(e,'move');send({type:'wheel',delta:Math.max(-4,Math.min(4,-e.deltaY/100))});e.preventDefault();},{passive:false});
screen.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key.length === 1) send({type:'text',text:e.key});
  else if (['Backspace','Delete','ArrowLeft','ArrowRight','Home','End','Enter','Escape'].includes(e.key)) send({type:'key',key:e.key});
  else return;
  e.preventDefault();
});
screen.addEventListener('paste', e => {send({type:'text',text:e.clipboardData.getData('text').slice(0,4096)});e.preventDefault();});
document.querySelector('#send').addEventListener('click', () => {const field=document.querySelector('#paste');send({type:'text',text:field.value.slice(0,4096)});field.value='';screen.focus();});
async function connect() {
  try {
    session = await (await fetch('/session')).json();
    frame = -1;
    while (true) {
      const response = await fetch('/frame?after='+frame);
      if (!response.ok) throw new Error('UI not ready');
      frame = Number(response.headers.get('X-Frame-Sequence'));
      const url = URL.createObjectURL(await response.blob());
      screen.src = url;
      await screen.decode();
      if (currentUrl) URL.revokeObjectURL(currentUrl);
      currentUrl = url;
      status.textContent = '已連線 · 原生 UI · EV6 預覽';
    }
  } catch {
    session = undefined;
    status.textContent = 'UI 啟動或重新載入中…';
    setTimeout(connect, 1000);
  }
}
connect();
