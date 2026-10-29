/* Pure dashboard state; loaded by browser and exercised with Node's test runner. */
(function(root) {
'use strict';
const numeric = v => typeof v === 'number' && Number.isFinite(v) && v >= 0;
const fresh = (p, now, ttl=45000) => typeof p.timestamp === 'number' && Number.isFinite(p.timestamp) && now-p.timestamp*1000 >= -5000 && now-p.timestamp*1000 <= ttl;
const STATES = new Set(['NORMAL','VIBRATION_DETECTED','ORIENTATION_CHANGE','HIGH_RISK_MOVEMENT','MINOR_MOVEMENT','SUSPICIOUS','HIGH_RISK','DISARMED','UNKNOWN','SENSOR_ERROR','DEVICE_OFFLINE']);
const remember = (map,key,value,limit=256) => { map.set(key,value); if(map.size>limit) map.delete(map.keys().next().value); };
function formatCommandStatus(r) {
  if(!r)return 'Belum ada perintah';
  const labels={sent:r.received.size?'Balasan sebagian diterima':'Dikirim · menunggu',
    acknowledged:'Diakui kedua HP',received_unconfirmed:'ACK diterima · ARM belum tersinkron',
    partial:'ACK sebagian · tenggat 12 dtk',timeout:'Timeout 12 dtk',rejected:'Ditolak',failed:'Gagal / koneksi terputus'};
  const devices=['HP_A','HP_B'].map(device=>r.received.has(device)?
    `${device} ${Math.round(r.rttMs[device])} ms${r.legacy.has(device)?' (format lama)':''}`:
    `${device} belum membalas`);
  return `${r.command}: ${labels[r.state]}${r.late?' · ACK terlambat':''} · ${devices.join(' · ')}`;
}
class DashboardModel {
  constructor() {
    this.connected=false; this.a={seen:0,armed:null,session:null,revision:-1,stamp:0};
    this.b={seen:0,armed:null,stamp:0}; this.sensor=null; this.status=null;
    this.points=[]; this.history=[]; this.events=new Map(); this.sessions=new Map();
    this.requests=new Map(); this.latestRequest=null;
    this.earlyB=new Map();
    this.presenceRetired=new Map();
    this.lastRejection=null;
  }
  connection(on) {
    this.connected=on;
    if(!on) {
      this.a.seen=this.b.seen=0; this.a.armed=this.b.armed=null;
      this.status=null;
      for(const r of this.requests.values()) if(r.state==='sent') r.state='failed';
    }
  }
  request(id,command,now=Date.now()) {
    const r={id,command,at:now,state:'sent',acks:new Set(),received:new Set(),rttMs:{},legacy:new Set()};
    remember(this.requests,id,r,50); this.latestRequest=r; return r;
  }
  failure(id) { const r=this.requests.get(id); if(r) r.state='failed'; }
  tick(now=Date.now()) {
    for(const r of this.requests.values()) if(r.state==='sent' && now-r.at>=12000) r.state=r.received.size?'partial':'timeout';
  }
  activity(device,now=Date.now()) {
    const d=device==='HP_A'?this.a:this.b;
    if(!this.connected || !d.seen || now-d.seen>45000) return 'Offline';
    return now-d.seen>15000?'Stale':'Online';
  }
  touch(device,p,now,evidence) {
    const d=device==='HP_A'?this.a:this.b;
    // Receipt of an old/delayed packet must not make it look freshly online.
    const seen=Math.min(now,p.timestamp*1000);
    if(seen>=d.seen) { d.seen=seen; d.evidence=evidence; d.issue=null; }
  }
  rejectTime(p,now) {
    if(['HP_A','HP_B'].includes(p.device)) {
      const age=typeof p.timestamp==='number'&&Number.isFinite(p.timestamp)?Math.round((now-p.timestamp*1000)/1000):null;
      const reason=age===null?'timestamp tidak valid':age < -5?`jam pengirim ${-age} dtk lebih maju`:`pesan berusia ${age} dtk; terlalu lama`;
      const d=p.device==='HP_A'?this.a:this.b;
      d.issue='Pesan ditolak: '+reason;
      this.lastRejection={device:p.device,reason,at:now};
    }
    return false;
  }
  presence(suffix,p,now) {
    if(suffix!=='presence/'+p.device || !['HP_A','HP_B'].includes(p.device)
      || typeof p.online!=='boolean' || typeof p.presence_session!=='string'
      || !p.presence_session || p.presence_session.length>128 || !numeric(p.started_at)) return false;
    const d=p.device==='HP_A'?this.a:this.b;
    const retired=this.presenceRetired.get(p.device)||new Set();
    if(retired.has(p.presence_session)) return false;
    if(d.presenceSession && p.presence_session!==d.presenceSession && p.started_at<d.presenceStarted) return false;
    if(!p.online) {
      // The broker's Will was prepared at startup. Its timestamp is not a death time.
      if(d.presenceSession && p.presence_session!==d.presenceSession) return false;
      if(Number.isInteger(p.sequence))d.presenceSequence=Math.max(d.presenceSequence||0,p.sequence);
      d.seen=0;d.armed=null;d.evidence='MQTT offline';
      return true;
    }
    if(!fresh(p,now))return this.rejectTime(p,now);
    if(!Number.isInteger(p.sequence) || p.sequence<1) return false;
    if(d.presenceSession===p.presence_session && p.sequence<=(d.presenceSequence||0)) return false;
    if(d.presenceSession && d.presenceSession!==p.presence_session) {
      retired.add(d.presenceSession);
      if(retired.size>256)retired.delete(retired.values().next().value);
      this.presenceRetired.set(p.device,retired);
      d.armed=null;
      if(p.device==='HP_A')this.status=null;
    }
    d.presenceSession=p.presence_session;d.presenceStarted=p.started_at;d.presenceSequence=p.sequence;
    d.coreVersion=typeof p.core_version==='string'?p.core_version.slice(0,40):'unknown';
    this.touch(p.device,p,now,'Heartbeat MQTT');
    return true;
  }
  accept(suffix,p,now=Date.now(),retained=false) {
    if(!p || typeof p!=='object' || Array.isArray(p)) return false;
    if(suffix.startsWith('presence/')) return this.presence(suffix,p,now);
    if(retained) return false;
    if(suffix==='status' && p.online===false && ['HP_A','HP_B'].includes(p.device)) {
      const d=p.device==='HP_A'?this.a:this.b; d.seen=0; d.armed=null;
      return true;
    }
    if(!fresh(p,now)) return this.rejectTime(p,now);
    if(suffix==='control') {
      if(p.device!=='HP_B' || !['ARM','DISARM','PING'].includes(p.command)
        || typeof p.request_id!=='string' || !fresh(p,now,15000)) return false;
      // A control sent by HP B proves activity, never that its requested ARM succeeded.
      this.touch('HP_B',p,now,'Kontrol '+p.command);
      return true;
    }
    if(suffix==='sensor/data') return this.telemetry(p,now);
    if(suffix==='ack') return this.ack(p,now);
    if(suffix==='status') return this.deviceStatus(p,now);
    return false;
  }
  telemetry(p,now) {
    if(p.device!=='HP_A' || typeof p.session!=='string' || !p.session || p.session.length>128 ||
      !Number.isInteger(p.sequence) || p.sequence<1 || !numeric(p.motion_rms) || !numeric(p.rotation_rad) || typeof p.simulated!=='boolean') return false;
    if(p.sequence<=(this.sessions.get(p.session)||0) || (this.sensor && p.timestamp<this.sensor.timestamp)) return false;
    if(this.a.session && p.session!==this.a.session && p.timestamp<=this.a.stamp) return false;
    if(p.session===this.a.session && Number.isInteger(p.state_revision) && p.state_revision<this.a.revision) return false;
    remember(this.sessions,p.session,p.sequence);
    this.sensor={...p,received:now}; this.touch('HP_A',p,now,'Telemetri');
    this.a.protocol=Number.isInteger(p.state_revision)?'current':'legacy';
    this.points.push({time:p.timestamp*1000,rms:p.motion_rms,rotation:p.rotation_rad,simulated:p.simulated});
    if(this.points.length>60) this.points.shift();
    return true;
  }
  authority(p,now) {
    if(typeof p.monitoring!=='boolean' || typeof p.session!=='string' || !p.session || !Number.isInteger(p.state_revision) || p.state_revision<0) return false;
    if(p.timestamp<this.a.stamp || (p.session===this.a.session && p.state_revision<this.a.revision)) return false;
    this.a={...this.a,armed:p.monitoring,session:p.session,revision:p.state_revision,stamp:p.timestamp,protocol:'current'};
    return true;
  }
  ack(p,now) {
    if(!['HP_A','HP_B'].includes(p.device) || !['ARM','DISARM','PING'].includes(p.command) || !fresh(p,now,15000)) return false;
    if(typeof p.monitoring!=='boolean' || typeof p.request_id!=='string')return false;
    if(p.result!==undefined && !['accepted','rejected'].includes(p.result))return false;
    this.touch(p.device,p,now,'ACK '+p.command);
    let current=false;
    if(p.device==='HP_A' && p.result==='accepted') {
      current=this.authority(p,now) || (p.session===this.a.session && p.state_revision===this.a.revision && p.monitoring===this.a.armed);
    }
    if(p.device==='HP_B' && p.result==='accepted' && typeof p.monitoring==='boolean') {
      // HP B must identify the same authority state before the UI calls it synchronized.
      if(p.authority_session===this.a.session && p.state_revision===this.a.revision && p.monitoring===this.a.armed) {
        if(p.timestamp>=this.b.stamp) this.b={...this.b,armed:p.monitoring,stamp:p.timestamp};
        current=true;
      } else if(typeof p.request_id==='string') remember(this.earlyB,p.request_id,p,50);
    }
    const r=this.requests.get(p.request_id);
    const legacy=p.device==='HP_A'?(!p.session || !Number.isInteger(p.state_revision) || p.result===undefined):
      (!p.authority_session || !Number.isInteger(p.state_revision) || p.result===undefined);
    const node=p.device==='HP_A'?this.a:this.b;
    if(legacy)node.protocol='legacy';
    if(r && r.command===p.command && p.timestamp*1000>=r.at-1000
      && ['sent','partial','timeout','received_unconfirmed'].includes(r.state)) {
      if(!r.received.has(p.device))r.rttMs[p.device]=Math.max(0,now-r.at);
      r.received.add(p.device);
      if(now-r.at>12000)r.late=true;
      if(legacy)r.legacy.add(p.device);
      if(p.result==='rejected') r.state='rejected';
      else {
        if(current)r.acks.add(p.device);
        if(r.acks.size===2)r.state='acknowledged';
        else if(r.received.size===2)r.state='received_unconfirmed';
        else if(now-r.at>=12000)r.state='partial';
      }
    }
    if(current && p.device==='HP_A' && this.earlyB.has(p.request_id)) {
      const early=this.earlyB.get(p.request_id); this.earlyB.delete(p.request_id); this.ack(early,now);
    }
    return true; // Liveness is valid even when authority synchronization is pending/legacy.
  }
  deviceStatus(p,now) {
    if(p.device==='HP_A') {
      if(!['ARMED','DISARMED','SENSOR_ERROR','UNKNOWN'].includes(p.status))return false;
      this.touch('HP_A',p,now,'Status HP A');
      if(this.authority(p,now)) this.a.error=p.status==='SENSOR_ERROR'?String(p.error||'Gagal baca sensor'):null;
      return true;
    }
    if(p.device!=='HP_B' || !STATES.has(p.status) || typeof p.alert!=='boolean') return false;
    this.touch('HP_B',p,now,'Status HP B');
    if(numeric(p.last_control_rtt_ms))this.b.controlRttMs=p.last_control_rtt_ms;
    this.b.protocol=typeof p.authority_session==='string'&&Number.isInteger(p.state_revision)?'current':'legacy';
    // Alarm-result updates may describe an earlier event: update its history only.
    const sameState=p.authority_session===this.a.session && p.state_revision===this.a.revision;
    if(p.timestamp>=this.b.stamp && sameState && p.alarm_update!==true) {
      this.b={...this.b,armed:typeof p.armed==='boolean'?p.armed:null,stamp:p.timestamp};
      this.status={...p,received:now};
    }
    if(p.alert && numeric(p.motion_rms) && numeric(p.rotation_rad) && typeof p.source_simulated==='boolean'
      && (p.source_timestamp===undefined || (numeric(p.source_timestamp) && p.source_timestamp<=p.timestamp+5))) {
      const key=typeof p.event_id==='string'?p.event_id:[p.timestamp,p.status,p.motion_rms,p.rotation_rad].join('|');
      const existing=this.events.get(key);
      if(existing) { existing.alarm_result=p.alarm_result||existing.alarm_result; return true; }
      const event={...p,time:p.source_timestamp||p.timestamp};
      remember(this.events,key,event,500); this.history.unshift(event);
      if(this.history.length>100) this.history.pop();
    }
    return true;
  }
  security(now=Date.now()) {
    if(!this.connected || this.activity('HP_A',now)==='Offline' || this.activity('HP_B',now)==='Offline') return 'DEVICE_OFFLINE';
    if(this.activity('HP_A',now)==='Stale' || this.activity('HP_B',now)==='Stale') return 'UNKNOWN';
    if(this.a.error) return 'SENSOR_ERROR';
    if(this.a.armed===false && this.b.armed===false) return 'DISARMED';
    if(this.a.armed!==true || this.b.armed!==true || !this.status) return 'UNKNOWN';
    if(this.status.authority_session!==this.a.session || this.status.state_revision!==this.a.revision) return 'UNKNOWN';
    if(this.status.status==='SENSOR_ERROR') return 'SENSOR_ERROR';
    const sourceAge=this.status.source_age_seconds;
    if(!numeric(sourceAge) || sourceAge+(now-this.status.received)/1000>15) return 'DEVICE_OFFLINE';
    return this.status.status;
  }
  csv() {
    const rows=[['Waktu_ISO','Klasifikasi','RMS_m_s2','Rotasi_rad','Sumber','Simulasi','Alarm_mode','Alarm_result','Event_ID'],
      ...this.history.map(e=>[new Date(e.time*1000).toISOString(),e.status,e.motion_rms,e.rotation_rad,e.source_device||'HP_A via HP_B',e.source_simulated,e.alarm_mode||'unknown',e.alarm_result||'unknown',e.event_id||''])];
    return '\ufeff'+rows.map(r=>r.map(v=>'"'+String(v).replaceAll('"','""')+'"').join(',')).join('\r\n');
  }
}
if(typeof module==='object' && module.exports) module.exports={DashboardModel,fresh,formatCommandStatus};
else {root.DashboardModel=DashboardModel;root.formatCommandStatus=formatCommandStatus;}
})(typeof globalThis==='object'?globalThis:this);
