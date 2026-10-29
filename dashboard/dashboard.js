'use strict';
const $=id=>document.getElementById(id);
const BROKER='wss://broker.hivemq.com:8884/mqtt';
const CORE_VERSION='2.1-presence';
const DASHBOARD_VERSION='2.1.1-ack';
let client=null, base='', model=new DashboardModel();
const rejectionLogs=new Map();
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clock=t=>new Date(t).toLocaleTimeString('id-ID',{hour12:false});
const date=t=>new Date(t).toLocaleString('id-ID',{hour12:false});
const labels={UNKNOWN:'BELUM TERKONFIRMASI',DISARMED:'MONITORING NONAKTIF',NORMAL:'GERAKAN STABIL',
 VIBRATION_DETECTED:'GETARAN TERDETEKSI',ORIENTATION_CHANGE:'PERUBAHAN ORIENTASI',HIGH_RISK_MOVEMENT:'GERAKAN RISIKO TINGGI',
 HIGH_RISK:'GERAKAN RISIKO TINGGI',SUSPICIOUS:'GERAKAN MENCURIGAKAN',MINOR_MOVEMENT:'GERAKAN RINGAN',SENSOR_ERROR:'GANGGUAN SENSOR',DEVICE_OFFLINE:'PERANGKAT / DATA OFFLINE'};
function log(text){const e=document.createElement('div');e.className='log-line';e.textContent=`[${clock(Date.now())}] ${text}`;$('logItems').prepend(e);while($('logItems').children.length>90)$('logItems').lastElementChild.remove();}
function connection(on,message){model.connection(on);$('connectionDot').classList.toggle('on',on);$('connectionText').textContent=on?'MQTT terhubung':'Tidak terhubung';$('mqttDetail').textContent=message;$('project').disabled=!!client;$('connect').disabled=!!client;$('disconnect').disabled=!client;for(const id of ['arm','disarm','ping'])$(id).disabled=!on;render();}
function command(cmd){if(!client?.connected)return;const id='dash_'+crypto.randomUUID();model.request(id,cmd);client.publish(base+'/control',JSON.stringify({device:'LAPTOP',command:cmd,request_id:id,timestamp:Date.now()/1000}),{qos:1,retain:false},err=>{if(err){model.failure(id);log('Publish gagal: '+err.message);render();}});log('CONTROL '+cmd+' dikirim; menunggu ACK HP A dan HP B');render();}
for(const [id,cmd]of [['arm','ARM'],['disarm','DISARM'],['ping','PING']])$(id).onclick=()=>command(cmd);
$('disconnect').onclick=()=>{const old=client;client=null;if(old)old.end(true);connection(false,'Koneksi diputuskan. Nilai terakhir tersimpan di layar sebagai riwayat sesi.');log('Disconnect manual');};
$('connect').onclick=()=>{
 const project=$('project').value.trim();
 if(!/^[a-zA-Z0-9_-]{8,48}$/.test(project)){log('Project ID harus 8–48 karakter huruf/angka/_/-');return;}
 if(typeof mqtt==='undefined'){log('MQTT.js tidak termuat; periksa koneksi CDN.');$('mqttDetail').textContent='Library MQTT.js gagal dimuat';return;}
 model=new DashboardModel();rejectionLogs.clear();base='smartpark/'+project;
 const instance=mqtt.connect(BROKER,{clientId:'smartpark_dash_'+crypto.randomUUID(),clean:true,reconnectPeriod:3000,connectTimeout:12000,queueQoSZero:false});client=instance;
 connection(false,'Menghubungkan ke HiveMQ...');
 instance.on('connect',()=>{
  if(client!==instance)return;
  instance.subscribe(['sensor/data','status','ack','control','presence/HP_A','presence/HP_B'].map(s=>base+'/'+s),{qos:1},(err,granted)=>{
   if(client!==instance)return;
   if(err||!granted||granted.some(g=>g.qos===128)){connection(false,'Subscribe gagal; koneksi belum siap.');log('SUBSCRIBE gagal');return;}
   connection(true,'Terhubung · '+base+' · dashboard '+DASHBOARD_VERSION+' · TLS WebSocket');log('MQTT dan subscribe siap; dashboard '+DASHBOARD_VERSION);command('PING');
  });
 });
 instance.on('message',(topic,buffer,packet)=>{
  if(client!==instance || buffer.length>16384 || !topic.startsWith(base+'/'))return;
  try{
   const p=JSON.parse(buffer.toString()),before=model.lastRejection;
   const accepted=model.accept(topic.slice(base.length+1),p,Date.now(),packet?.retain===true);
   if(accepted&&p.alert)log('Event HP B: '+p.status+' · alarm='+p.alarm_result);
   if(model.lastRejection!==before){const issue=model.lastRejection,key=issue.device+issue.reason;
    if(!rejectionLogs.has(key)||Date.now()-rejectionLogs.get(key)>15000){log(issue.device+': '+issue.reason);rejectionLogs.set(key,Date.now());if(rejectionLogs.size>100)rejectionLogs.delete(rejectionLogs.keys().next().value);}}
   render();
  }
  catch(err){log('Payload diabaikan: '+err.message);}
 });
 instance.on('close',()=>{if(client===instance)connection(false,'Broker terputus; mencoba ulang. Menunggu bukti perangkat baru.');});
 instance.on('reconnect',()=>{if(client===instance)$('mqttDetail').textContent='Menghubungkan ulang...';});
 instance.on('error',err=>{if(client===instance)log('MQTT error: '+err.message);});
};
function render(){
 const now=Date.now();model.tick(now);
 const s=model.sensor;
 $('rms').textContent=s?s.motion_rms.toFixed(4):'—';$('rotation').textContent=s?s.rotation_rad.toFixed(4):'—';
 $('freshness').textContent=s?Math.max(0,Math.round((now-s.timestamp*1000)/1000))+' dtk':'—';
 $('demoLabel').textContent=s?(s.simulated?'DATA SIMULASI':'DATA FISIK · KLAIM HP A'):'MENUNGGU TELEMETRI';
 $('sampleKind').textContent=s?(s.simulated?'Simulasi':'Sensor fisik (klaim HP A)'):'Sumber belum diketahui';
 $('rmsLevel').textContent=s?'Nilai terakhir diterima':'Belum ada data';
 $('rotationLevel').textContent=s?'Integral per window · '+(s.window_seconds??'?')+' dtk':'Belum ada data';
 $('lastUpdated').textContent='Telemetri terakhir: '+(s?date(s.timestamp*1000):'—');
 for(const [node,id] of [['HP_A','edgeA'],['HP_B','edgeB']]){
  const d=node==='HP_A'?model.a:model.b, activity=model.activity(node,now);
  $(id).textContent=activity+' · '+(d.armed===null?'ARM belum diketahui':d.armed?'ARM':'DISARM');
  $(id).className='device-badge'+(activity==='Online'?' online':activity==='Stale'?' stale':'');
  $(id+'Time').textContent=(d.evidence||'Bukti terakhir')+': '+(d.seen?date(d.seen):'—')+(d.coreVersion?' · core '+d.coreVersion:' · heartbeat belum terdeteksi')+
    (d.protocol==='legacy'?' · payload lama; ARM belum tersinkron':'')+(d.issue?' · '+d.issue:'')+
    (node==='HP_B'&&typeof d.controlRttMs==='number'?' · RTT B→A→B: '+Math.round(d.controlRttMs)+' ms':'');
 }
 $('armedStatus').textContent='HP A / HP B: '+[model.a,model.b].map(d=>d.armed===null?'?':d.armed?'ARM':'DISARM').join(' / ');
 const state=model.security(now), alert=['HIGH_RISK_MOVEMENT','HIGH_RISK','SUSPICIOUS'].includes(state);
 $('securityState').textContent=labels[state]||state;
 $('securityDesc').textContent=state==='DISARMED'?'Kedua perangkat mengonfirmasi monitoring nonaktif.':state==='SENSOR_ERROR'?(model.a.error||'HP B melaporkan gangguan sensor.'):state==='UNKNOWN'||state==='DEVICE_OFFLINE'?'Menunggu status perangkat dan telemetri terbaru; kondisi kendaraan belum dapat dinilai.':'Klasifikasi pergerakan oleh HP B; periksa kendaraan bila ada peringatan.';
 $('securityChip').textContent='● '+state.replaceAll('_',' ');$('securityChip').className='status-chip'+(alert?' alert':state==='NORMAL'?' ok':'');
 $('ackText').textContent=formatCommandStatus(model.latestRequest);
 $('alerts').textContent=String(model.history.length);
 $('emptyRms').classList.toggle('hidden',model.points.length>0);$('emptyRot').classList.toggle('hidden',model.points.length>0);
 renderHistory();drawAll();
}
function renderHistory(){
 $('historyRows').innerHTML=model.history.length?model.history.map((h,i)=>`<tr><td>${i+1}</td><td>${esc(date(h.time*1000))}</td><td><span class="alert-tag">${esc(labels[h.status]||h.status)}</span><small>${esc(h.alarm_mode||'unknown')} · ${esc(h.alarm_result||'unknown')}</small></td><td>${h.motion_rms.toFixed(4)}</td><td>${h.rotation_rad.toFixed(4)}</td><td>${h.source_simulated?'Simulasi':'Fisik (klaim HP A)'}<small>HP A → HP B · #${esc(h.source_sequence??'?')}</small></td></tr>`).join(''):'<tr><td colspan="6" class="blank">Belum ada peringatan tercatat pada sesi ini.</td></tr>';
}
function drawAll(){drawChart('rmsChart','rms','#3776c8');drawChart('rotChart','rotation','#0f9878');}
function drawChart(id,key,color){
 const c=$(id),rect=c.getBoundingClientRect(),dpr=Math.max(1,window.devicePixelRatio||1);
 const w=Math.max(240,rect.width||400),h=Math.max(120,rect.height||240);c.width=Math.round(w*dpr);c.height=Math.round(h*dpr);
 const ctx=c.getContext('2d');ctx.scale(dpr,dpr);ctx.clearRect(0,0,w,h);
 const pad={l:45,r:11,t:14,b:27},pw=w-pad.l-pad.r,ph=h-pad.t-pad.b,xs=model.points;
 const ymax=Math.max(.5,...xs.map(x=>x[key]*1.2));ctx.font='11px system-ui';ctx.textAlign='right';ctx.lineWidth=1;
 for(let i=0;i<=4;i++){const y=pad.t+ph*i/4;ctx.strokeStyle='#edf0f3';ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke();ctx.fillStyle='#9fa5ad';ctx.fillText((ymax*(1-i/4)).toFixed(1),pad.l-8,y+4);}
 if(!xs.length)return;
 const start=xs[0].time,span=xs.at(-1).time-start;
 const xAt=i=>pad.l+pw*(span?(xs[i].time-start)/span:.5);
 ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();xs.forEach((v,i)=>{const x=xAt(i),y=pad.t+ph*(1-v[key]/ymax);if(!i||v.time-xs[i-1].time>15000||v.simulated!==xs[i-1].simulated)ctx.moveTo(x,y);else ctx.lineTo(x,y);});ctx.stroke();
 ctx.fillStyle='#9ca2aa';for(const i of new Set([0,Math.floor((xs.length-1)/2),xs.length-1])){ctx.textAlign=i===0?'left':i===xs.length-1?'right':'center';ctx.fillText(clock(xs[i].time),xAt(i),h-6);}
}
$('exportCsv').onclick=()=>{if(!model.history.length){log('Belum ada event untuk diekspor');return;}const u=URL.createObjectURL(new Blob([model.csv()],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=u;a.download='SmartPark_Alert_History.csv';a.click();setTimeout(()=>URL.revokeObjectURL(u),500);};
$('clearLogs').onclick=()=>{$('logItems').replaceChildren();log('Log dibersihkan');};
$('project').addEventListener('keydown',e=>{if(e.key==='Enter'&&!client)$('connect').click();});
window.addEventListener('resize',drawAll);setInterval(render,1000);
connection(false,'Hubungkan MQTT. Riwayat maksimal 100 event hanya tersimpan di memori tab; reset saat menghubungkan ulang manual atau memuat ulang.');
log('Dashboard '+DASHBOARD_VERSION+' siap; ACK diterima dan sinkronisasi ARM ditampilkan terpisah.');
