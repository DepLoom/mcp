"""Bounded official reference reads. No project bytes, credentials or execution grants."""
from __future__ import annotations
import hashlib,http.client,ipaddress,json,math,os,re,socket,ssl,time
from pathlib import Path
from urllib.parse import quote,urlsplit

VERSION='official-reference-provider-v1'
TTL=86400
SHA=re.compile(r'[0-9a-f]{40}\Z')
HEX=re.compile(r'[0-9a-f]{64}\Z')
ORIGINS={'api.github.com','raw.githubusercontent.com'}
CATALOG={
 ('strip-json-comments','3.1.1','5.0.0'):[
  ('beforePackage','sindresorhus/strip-json-comments','v3.1.1','package.json'),
  ('afterPackage','sindresorhus/strip-json-comments','v5.0.0','package.json'),
  ('beforeSource','sindresorhus/strip-json-comments','v3.1.1','index.js'),
  ('beforeTypes','sindresorhus/strip-json-comments','v3.1.1','index.d.ts'),
  ('afterSource','sindresorhus/strip-json-comments','v5.0.0','index.js'),
  ('afterTypes','sindresorhus/strip-json-comments','v5.0.0','index.d.ts'),
  ('afterReadme','sindresorhus/strip-json-comments','v5.0.0','readme.md')],
 ('vite','6.4.5','7.0.0'):[
  ('beforePackage','vitejs/vite','v6.4.5','packages/vite/package.json'),
  ('afterPackage','vitejs/vite','v7.0.0','packages/vite/package.json'),
  ('migrationGuide','vitejs/vite','v7.0.0','docs/guide/migration.md'),
  ('companionPackage','vitejs/vite-plugin-react','plugin-react@4.5.2','packages/plugin-react/package.json')],
}
class EvidenceUnavailable(ValueError):pass

def digest(raw):return hashlib.sha256(raw).hexdigest()
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')

def validate_url(url):
    parts=urlsplit(url)
    if (parts.scheme!='https' or parts.hostname not in ORIGINS or parts.username or parts.password
            or parts.port not in (None,443) or parts.query or parts.fragment or not parts.path.startswith('/')
            or '\\' in parts.path or any(x in parts.path for x in ('\r','\n','\x00'))):
        raise EvidenceUnavailable('OFFICIAL_ORIGIN_REJECTED')
    return parts

def public_addresses(host):
    rows=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
    addresses=[]
    for row in rows:
        ip=ipaddress.ip_address(row[4][0]);mapped=getattr(ip,'ipv4_mapped',None)
        if not ip.is_global or ip.is_multicast or (mapped is not None and not mapped.is_global):
            raise EvidenceUnavailable('NON_PUBLIC_DNS_ADDRESS')
        if str(ip) not in addresses:addresses.append(str(ip))
    if not addresses:raise EvidenceUnavailable('OFFICIAL_DNS_UNAVAILABLE')
    return addresses

class _PinnedHTTPS(http.client.HTTPSConnection):
    """Connect to the validated address, validate TLS for the original host."""
    def __init__(self,host,ip,timeout):
        super().__init__(host,timeout=timeout,context=ssl.create_default_context());self._validated_ip=ip
    def connect(self):
        raw=socket.create_connection((self._validated_ip,443),timeout=self.timeout)
        try:self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
        except BaseException:raw.close();raise

class OfficialHTTP:
    authority='OFFICIAL_PUBLIC_HTTPS_NO_AUTH'
    def read(self,url,*,deadline,max_bytes):
        parts=validate_url(url);addresses=public_addresses(parts.hostname)
        remaining=deadline-time.monotonic()
        if remaining<=0:raise EvidenceUnavailable('EVIDENCE_DEADLINE')
        connection=_PinnedHTTPS(parts.hostname,addresses[0],min(5.0,remaining))
        try:
            connection.request('GET',parts.path,headers={'User-Agent':'DepLoom-Official-Evidence/1',
                'Accept':'application/vnd.github+json, text/plain','Accept-Encoding':'identity'})
            response=connection.getresponse()
            if 300<=response.status<400:raise EvidenceUnavailable('OFFICIAL_REDIRECT_BLOCKED')
            if response.status!=200:raise EvidenceUnavailable('OFFICIAL_HTTP_'+str(response.status))
            if response.getheader('Content-Encoding','identity').lower() not in ('','identity'):
                raise EvidenceUnavailable('UNSUPPORTED_CONTENT_ENCODING')
            length=response.getheader('Content-Length')
            if length is not None and (not length.isdigit() or int(length)>max_bytes):
                raise EvidenceUnavailable('DOCUMENT_BYTE_LIMIT')
            chunks=[];total=0
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise EvidenceUnavailable('EVIDENCE_DEADLINE')
                if connection.sock:connection.sock.settimeout(min(5.0,remaining))
                data=response.read(min(16384,max_bytes-total+1))
                if not data:break
                total+=len(data)
                if total>max_bytes:raise EvidenceUnavailable('DOCUMENT_BYTE_LIMIT')
                chunks.append(data)
            return b''.join(chunks)
        finally:connection.close()

class SourceStore:
    """Cache is reference data only, never an execution receipt or authority."""
    def __init__(self,root,*,deadline,transport=None,clock=time.time,offline=False):
        self.root=Path(root);self.deadline=deadline;self.clock=clock;self.offline=offline
        self.transport=transport if transport is not None else OfficialHTTP()
        self.authority=OfficialHTTP.authority if transport is None else 'ENGINEERING_CONTROL_TRANSPORT'
        self.metrics={'httpTransactions':0,'responseBodyBytes':0,'responseBodyBytesComplete':True,'cacheHits':0,'cacheMisses':0}
        self._http_memo={};self._stream_failed=False
        self._pins={};self._bodies={}
        self._safe(self.root)
    def _safe(self,path):
        absolute=Path(os.path.abspath(path));root=Path(os.path.abspath(self.root))
        if not absolute.is_relative_to(root):raise EvidenceUnavailable('CACHE_PATH_REJECTED')
        for part in [absolute,*absolute.parents]:
            if part.is_symlink() or (hasattr(part,'is_junction') and part.is_junction()):raise EvidenceUnavailable('CACHE_LINK_REJECTED')
        return absolute
    def _load(self,path):
        path=self._safe(path)
        if not path.exists():return None
        if not path.is_file() or path.stat().st_size>1048576:raise EvidenceUnavailable('CACHE_RECORD_INVALID')
        try:wrapper=json.loads(path.read_bytes());record=wrapper['record'];expected=wrapper['sha256'];assert set(wrapper)=={'record','sha256'} and digest(encoded(record))==expected
        except (ValueError,OSError,KeyError,TypeError,AssertionError):raise EvidenceUnavailable('CACHE_RECORD_INVALID') from None
        return record
    def _save(self,path,raw):
        path=self._safe(path);path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_name(path.name+'.'+os.urandom(8).hex()+'.tmp')
        try:
            with temp.open('xb') as stream:stream.write(raw)
            self._safe(path);temp.replace(path)
        finally:
            if temp.exists():temp.unlink()
    def _fetch(self,url):
        validate_url(url)
        if self.offline:raise EvidenceUnavailable('EVIDENCE_OFFLINE')
        cached=self._http_memo.get(url)
        if isinstance(cached,EvidenceUnavailable):raise cached
        if isinstance(cached,bytes):return cached
        if self._stream_failed:raise EvidenceUnavailable('PREVIOUS_HTTP_STREAM_UNPROVEN')
        if time.monotonic()>=self.deadline:raise EvidenceUnavailable('EVIDENCE_DEADLINE')
        if self.metrics['httpTransactions']>=12:raise EvidenceUnavailable('HTTP_TRANSACTION_LIMIT')
        self.metrics['httpTransactions']+=1
        remaining_bytes=1048576-self.metrics['responseBodyBytes']
        if remaining_bytes<=0:raise EvidenceUnavailable('TOTAL_BODY_BYTE_LIMIT')
        try:raw=self.transport.read(url,deadline=self.deadline,max_bytes=min(131072,remaining_bytes))
        except (EvidenceUnavailable,OSError,ValueError,http.client.HTTPException) as exc:
            failure=exc if isinstance(exc,EvidenceUnavailable) else EvidenceUnavailable('OFFICIAL_TRANSPORT_UNAVAILABLE')
            self.metrics['responseBodyBytesComplete']=False;self._stream_failed=True;self._http_memo[url]=failure
            raise failure from None
        if not isinstance(raw,bytes):raise EvidenceUnavailable('OFFICIAL_BODY_INVALID')
        self.metrics['responseBodyBytes']+=len(raw)
        if len(raw)>131072 or self.metrics['responseBodyBytes']>1048576:raise EvidenceUnavailable('DOCUMENT_BYTE_LIMIT')
        self._http_memo[url]=raw
        return raw
    def _json(self,url):
        raw=self._fetch(url)
        try:return json.loads(raw)
        except (ValueError,UnicodeError):raise EvidenceUnavailable('OFFICIAL_METADATA_INVALID') from None
    def pin(self,repo,tag):
        key=(repo,tag)
        if key in self._pins:return self._pins[key]
        allowed={(r,t) for rows in CATALOG.values() for _,r,t,_ in rows}
        if key not in allowed:raise EvidenceUnavailable('UNVERIFIED_PACKAGE_REPOSITORY')
        cache_key=digest(encoded([VERSION,self.authority,repo,tag]))
        path=self.root/'pins'/(cache_key+'.json');record=self._load(path);now=self.clock()
        if record is not None:
            if (not isinstance(record,dict) or set(record)!={'repo','tag','commit','observedAt','transport'}
                    or record['repo']!=repo or record['tag']!=tag or record['transport']!=self.authority
                    or not isinstance(record['commit'],str) or not SHA.fullmatch(record['commit'])
                    or isinstance(record['observedAt'],bool) or not isinstance(record['observedAt'],(int,float)) or not math.isfinite(record['observedAt'])):
                raise EvidenceUnavailable('CACHE_PIN_IDENTITY_INVALID')
            age=now-record['observedAt']
            if age<0:raise EvidenceUnavailable('CACHE_CLOCK_WENT_BACKWARD')
            if age<=TTL:self.metrics['cacheHits']+=1;self._pins[key]=record;return record
        self.metrics['cacheMisses']+=1
        if self.offline:raise EvidenceUnavailable('STALE_OR_MISSING_PIN_OFFLINE')
        url='https://api.github.com/repos/'+repo+'/git/ref/tags/'+quote(tag,safe='')
        data=self._json(url)
        if not isinstance(data,dict) or data.get('ref')!='refs/tags/'+tag:raise EvidenceUnavailable('OFFICIAL_TAG_IDENTITY_INVALID')
        obj=data.get('object')
        for _ in range(3):
            if not isinstance(obj,dict) or not isinstance(obj.get('sha'),str) or not SHA.fullmatch(obj['sha']):
                raise EvidenceUnavailable('OFFICIAL_COMMIT_INVALID')
            if obj.get('type')=='commit':break
            if obj.get('type')!='tag':raise EvidenceUnavailable('OFFICIAL_TAG_OBJECT_UNSUPPORTED')
            obj=self._json('https://api.github.com/repos/'+repo+'/git/tags/'+obj['sha']).get('object')
        else:raise EvidenceUnavailable('ANNOTATED_TAG_DEPTH_LIMIT')
        commit=obj['sha']
        if record is not None and record['commit']!=commit:raise EvidenceUnavailable('PIN_DRIFT')
        record={'repo':repo,'tag':tag,'commit':commit,'observedAt':now,'transport':self.authority}
        self._save(path,encoded({'record':record,'sha256':digest(encoded(record))}));self._pins[key]=record;return record
    def document(self,kind,repo,tag,artifact):
        pin=self.pin(repo,tag);key=(repo,pin['commit'],artifact)
        if key in self._bodies:return self._bodies[key]
        if (kind,repo,tag,artifact) not in [x for rows in CATALOG.values() for x in rows]:
            raise EvidenceUnavailable('OFFICIAL_ARTIFACT_REJECTED')
        source_key=digest(encoded([VERSION,self.authority,*key]));path=self.root/'sources'/(source_key+'.json');record=self._load(path)
        url='https://raw.githubusercontent.com/'+repo+'/'+pin['commit']+'/'+artifact
        if record is not None:
            if (not isinstance(record,dict) or set(record)!={'url','sha256','bytes','transport'} or record['url']!=url
                    or record['transport']!=self.authority or not isinstance(record['sha256'],str) or not HEX.fullmatch(record['sha256'])):
                raise EvidenceUnavailable('CACHE_SOURCE_IDENTITY_INVALID')
            blob=self._safe(self.root/'blobs'/record['sha256'])
            if not blob.is_file() or blob.stat().st_size>131072:raise EvidenceUnavailable('CACHE_BODY_INVALID')
            raw=blob.read_bytes()
            if digest(raw)!=record['sha256'] or len(raw)!=record['bytes']:raise EvidenceUnavailable('CACHE_HASH_MISMATCH')
            self.metrics['cacheHits']+=1;cache_status='HIT'
        else:
            self.metrics['cacheMisses']+=1;raw=self._fetch(url)
            record={'url':url,'sha256':digest(raw),'bytes':len(raw),'transport':self.authority}
            self._save(self.root/'blobs'/record['sha256'],raw);self._save(path,encoded({'record':record,'sha256':digest(encoded(record))}));cache_status='MISS'
        try:text=raw.decode('utf-8')
        except UnicodeError:raise EvidenceUnavailable('OFFICIAL_DOCUMENT_NOT_UTF8') from None
        value={'kind':kind,'repo':repo,'tag':tag,'resolvedCommit':pin['commit'],'artifact':artifact,'url':url,
            'sha256':record['sha256'],'bytes':len(raw),'verified':True,'transport':self.authority,
            'retrievedAt':pin['observedAt'],'freshness':'WITHIN_PIN_TTL','cacheStatus':cache_status,'text':text}
        self._bodies[key]=value;return value
