"""Local browser integration fixture. NO cloud and NO physical sensor/alarm."""
import json
import logging
from pathlib import Path
import sys
import threading
import time
import argparse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--scenario',choices=('normal','presence-only','legacy-b-only'),default='normal')
scenario=parser.parse_args().scenario
sys.path.insert(0,str(ROOT))
from smartpark.edge_a import SensorNode
from smartpark.edge_b import ReceiverNode
from smartpark.sensor import SimulatedSensor
from smartpark.alarm import Alarm
from smartpark.config import Settings

MESSAGES=[]
NODES=[]
BUSLOCK=threading.RLock()

class LocalLink:
    def __init__(self,settings,role,subscriptions,handler):
        self.settings=settings;self.subscriptions=subscriptions;self.handler=handler;self.on_connection=None
        NODES.append(self)
    def publish(self,suffix,data):
        with BUSLOCK:
            MESSAGES.append({'topic':self.settings.topic(suffix),'payload':data.copy()})
            for node in NODES[:]:
                if suffix in node.subscriptions:node.handler(self.settings.topic(suffix),data.copy())
        return True

settings=Settings(interval=1,project='smartpark_test_20261029_cac')
with patch('smartpark.edge_a.MQTTLink',LocalLink),patch('smartpark.edge_b.MQTTLink',LocalLink):
    a=SensorNode(settings,SimulatedSensor());a.source.set_mode('high')
    b=ReceiverNode(settings,Alarm())

def sampling():
    while True:
        if scenario!='normal':
            with BUSLOCK:
                now=time.time()
                if scenario=='legacy-b-only':
                    MESSAGES.append({'topic':settings.topic('control'),'payload':{
                        'device':'HP_B','command':'PING','request_id':'legacy_'+str(now),'timestamp':now}})
                else:
                    for device in ('HP_A','HP_B'):
                        MESSAGES.append({'topic':settings.topic('presence/'+device),'payload':{
                            'device':device,'online':True,'core_version':'2.1-presence',
                            'presence_session':'fixture_'+device,'started_at':1,
                            'sequence':int(now*1000),'timestamp':now},'retained':True})
            time.sleep(2)
            continue
        with BUSLOCK:
            if a.armed:
                # Deterministic fixture timestamps still represent simulated samples only.
                samples=[]
                for i in range(5):
                    sample=a.source.read();sample['t']=i*.4;samples.append(sample)
                from smartpark.processing import process_window
                a.seq+=1
                a.link.publish('sensor/data',{'device':'HP_A','session':a.session,'sequence':a.seq,
                    'state_revision':a.revision,'monitoring':True,'simulated':True,
                    'timestamp':time.time(),**process_window(samples)})
                while not b.jobs.empty():b.process_alarm(b.jobs.get_nowait())
            a.publish_state();b.publish_state()
        time.sleep(2)

STUB=r"""
window.mqtt={connect(){
 let handlers={},cursor=-1;
 const c={connected:false,on(name,fn){handlers[name]=fn;return c;},
 subscribe(topics,options,cb){cb(null,topics.map(topic=>({topic,qos:1})));},
 publish(topic,payload,options,cb){fetch('/command',{method:'POST',body:payload}).then(()=>cb?.()).catch(cb);},
 end(){clearInterval(c.timer);c.connected=false;}};
 c.timer=setInterval(async()=>{if(cursor<0)return;try{const reply=await fetch('/events?cursor='+cursor).then(r=>r.json());cursor=reply.cursor;for(const m of reply.messages)handlers.message?.(m.topic,{toString:()=>JSON.stringify(m.payload),length:JSON.stringify(m.payload).length},{retain:m.retained===true});}catch(e){handlers.error?.(e);}},250);
 fetch('/events?cursor=-1').then(r=>r.json()).then(reply=>{cursor=reply.cursor;c.connected=true;handlers.connect?.();});return c;
}};
"""
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path.startswith('/events'):
            cursor=int(self.path.split('cursor=')[-1]);
            with BUSLOCK: body=json.dumps({'messages':MESSAGES[cursor:] if cursor>=0 else [],'cursor':len(MESSAGES)}).encode()
            ctype='application/json'
        elif self.path=='/mqtt-stub.js':body=STUB.encode();ctype='text/javascript'
        elif self.path=='/' or self.path=='/index.html':
            body=(ROOT/'dashboard'/'index.html').read_text(encoding='utf-8-sig').replace('https://unpkg.com/mqtt@5.15.1/dist/mqtt.min.js','/mqtt-stub.js').replace('<main class="wrap">','<main class="wrap"><p style="padding:12px;background:#fff0bc;font-weight:bold">UJI LOKAL — TRANSPORT MOCK · SENSOR SIMULASI · TANPA MQTT CLOUD / GETARAN FISIK</p>').replace('value="smartpark_demo_2026"','value="smartpark_test_20261029_cac"').encode();ctype='text/html; charset=utf-8'
        else:
            name=self.path.split('?')[0].lstrip('/')
            if name not in ('dashboard.js','dashboard-model.js','style.css'):self.send_error(404);return
            body=(ROOT/'dashboard'/name).read_bytes();ctype='text/css' if name.endswith('.css') else 'text/javascript'
        self.send_response(200);self.send_header('Content-Type',ctype);self.end_headers();self.wfile.write(body)
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        with BUSLOCK:
            for node in NODES[:]:
                if scenario=='normal' and 'control' in node.subscriptions:node.handler(settings.topic('control'),data.copy())
        self.send_response(204);self.end_headers()
threading.Thread(target=sampling,daemon=True).start()
print('LOCAL FIXTURE http://127.0.0.1:8001 (simulated only)',flush=True)
ThreadingHTTPServer(('127.0.0.1',8001),Handler).serve_forever()

