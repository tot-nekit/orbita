// Disposable local server: never reads the project's .env or contacts providers.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {fileURLToPath} from 'node:url';

export const botToken='test:token';
export async function startServer(options={}){
 const code=String.raw`
import os,sys,signal,tempfile,threading,json
from pathlib import Path
original_exists=Path.exists
Path.exists=lambda p: False if p.name=='.env' else original_exists(p)
def audit(event,args):
    if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)) and os.path.basename(os.fsdecode(args[0]))=='.env':
        raise RuntimeError('Test must not read .env')
    if event=='socket.connect' and isinstance(args[1],tuple) and args[1][0] not in ('127.0.0.1','::1','localhost'):
        raise RuntimeError('External network disabled in tests')
    if event=='socket.getaddrinfo' and args[0] not in ('127.0.0.1','::1','localhost',None):
        raise RuntimeError('External DNS disabled in tests')
sys.addaudithook(audit)
os.environ.clear()
os.environ.update(APP_SESSION_SECRET='disposable-ui-test-secret-'+'x'*48,OPENAI_API_KEY='',TELEGRAM_BOT_TOKEN='test:token',TELEGRAM_ALLOWED_USER_IDS='101,202',TELEGRAM_ALLOW_PUBLIC='0',COOKIE_SECURE='0')
os.environ['AUTH_REQUIRED']=sys.argv[1]
from app import store
from app.server import Handler
from http.server import ThreadingHTTPServer
signal.signal(signal.SIGTERM,lambda *_: sys.exit(0))
with tempfile.TemporaryDirectory(prefix='orbita-ui-') as tmp:
    store.DB_PATH=str(Path(tmp)/'test.sqlite3')
    store.init()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    print(json.dumps({'port':server.server_port}),flush=True)
    try: server.serve_forever()
    finally: server.server_close()
`;
 const child=spawn(process.env.PYTHON||'python3',['-B','-c',code,options.auth?'1':'0'],{cwd:fileURLToPath(new URL('../',import.meta.url)),stdio:['ignore','pipe','pipe']});
 child.stderr.on('data',()=>{}); // Never echo server internals or provider credentials.
 const lines=createInterface({input:child.stdout});
 try{
  const base=await new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>reject(new Error('Disposable test server startup timed out')),15000);
   const fail=()=>{clearTimeout(timer);reject(new Error('Disposable test server failed to start'));};
   child.once('error',fail);child.once('exit',fail);
   lines.once('line',line=>{clearTimeout(timer);child.off('error',fail);child.off('exit',fail);try{const {port}=JSON.parse(line);if(!Number.isInteger(port)||port<=0)throw Error();resolve('http://127.0.0.1:'+port);}catch{reject(new Error('Invalid disposable server address'));}});
  });
  return {base,async stop(){lines.close();if(child.exitCode!==null)return;await new Promise(resolve=>{child.once('exit',resolve);child.kill('SIGTERM');});}};
 }catch(e){lines.close();child.kill('SIGTERM');throw e;}
}

export async function localOnly(context,base){
 await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
}
