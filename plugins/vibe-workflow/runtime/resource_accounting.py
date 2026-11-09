"""A0 accounting experiment only: no context, permission, retention or SQL authority."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import math
import sqlite3
import time
from types import MappingProxyType
from runtime.recovery_uses import CaptureBudget, UseContractError, MAX_CAPTURE_BYTES
from runtime import startup

REGISTRATION_BYTES = 1024
_ACQUISITION = ContextVar('resource_acquisition',default=None)
_VALIDATION = ContextVar('resource_decoder_validation',default=None)


def _json_text_scan(text,*,depth_limit=None,unit_limit=None):
    """Accounting measurements only; the original loader owns JSON validation."""
    kind=type(text);require(kind in (str,bytes))
    depth=units=bmp=nonbmp=scalar_starts=0;quoted=escaped=scalar=False
    for item in text:
        code=item if kind is bytes else ord(item)
        if code>=65536:nonbmp+=1
        elif code>=127:bmp+=1
        if quoted:
            if escaped:escaped=False
            elif code==92:escaped=True
            elif code==34:quoted=False
            continue
        if code==34:units+=1;quoted=True;scalar=False
        elif code in (123,91):units+=1;depth+=1;scalar=False
        elif code in (125,93):depth=max(0,depth-1);scalar=False
        elif code in (32,9,10,13,44,58):scalar=False
        elif not scalar:units+=1;scalar_starts+=1;scalar=True
        if (depth_limit is not None and depth>depth_limit or unit_limit is not None and units>unit_limit):
            raise Exhausted('Resource decoder structure exhausted')
    return len(text),units,bmp,nonbmp,scalar_starts


def _json_comparison_bound(raw):
    """Upper bound compact ASCII JSON material, without granting validation authority."""
    require(type(raw) in (str,bytes))
    if type(raw) is bytes and (not raw.isascii() or b'\0' in raw):return 44*len(raw)+64
    chars,units,bmp,nonbmp,scalar_starts=_json_text_scan(raw)
    return chars+5*bmp+11*nonbmp+32*scalar_starts+64


class Acquisition:
    """Explicit accounting owner; physical context exit never disposes returned values."""
    def __init__(self,resources,bookkeeping):
        self.resources=resources;self._bookkeeping=bookkeeping;self.leases={id(bookkeeping):bookkeeping}
        self.value=self.error=self.operation=self.token=None;self.args=();self.kwargs={};self._owned_decodes={};self._source_uses={}
    def _ready(self):
        try:
            require(self.token is None and self.resources._acquisitions.get(id(self)) is self)
            self.resources.check_reusable()
        except Exhausted as error:error._vcw_resource_origin=self.resources;raise
    def bind(self,operation,*args,**kwargs):
        self._ready()
        require(self.operation is None and self.token is None)
        self.operation=operation;self.args=args;self.kwargs=kwargs
        return self
    @contextmanager
    def source_use(self,context,source,metadata,subject):
        """Enter only the code-owned permission file phase, retaining its caller aliases."""
        from runtime import evidence,state
        current=_ACQUISITION.get();scope=state._CAPTURE_SQL_SCOPE.get()
        if current is None:
            self._ready()
            require(self.operation is None and not self.has_output_or_error)
        else:
            require(current is self and self.token is not None)
            self.resources.check_reusable()
        require(scope is None or scope[0] is self.resources and scope[1] is None)
        use=token=None
        try:
            registration=self.reserve(requested=1024,live=4096)
            use=_SourceUse(self,context,source,metadata,subject,registration)
            self._source_uses[id(use)]=use
            if current is None:
                token=_ACQUISITION.set(self);self.token=token
            use.value=evidence._source_current_permission_files(context,source,subject,resources=self.resources,held=use)
            if token is not None:self.value=use.value
            self.resources.check()
            yield use.value
            self.resources.check()
        except BaseException as error:
            if use is not None:use.error=error
            self.fail(error);raise
        finally:
            if token is not None:_ACQUISITION.reset(token);self.token=None
    def __enter__(self):
        self._ready();require(self.operation is not None)
        from runtime import state
        scope=state._CAPTURE_SQL_SCOPE.get()
        if scope is None or scope[0] is not self.resources or scope[1] is not None:
            error=Exhausted('Acquisition lacks its registered reader resource scope');error._vcw_resource_origin=self.resources;raise error
        token=_ACQUISITION.set(self);self.token=token
        try:
            self.value=self.operation(*self.args,**self.kwargs);self.resources.check()
        except BaseException as error:
            try:self.fail(error)
            finally:_ACQUISITION.reset(token);self.token=None
            raise
        return self.value
    def __exit__(self,kind,error,traceback):
        token=self.token;require(token is not None and _ACQUISITION.get() is self)
        try:
            if error is not None:self.fail(error)
            else:
                try:self.resources.check()
                except BaseException as failed:self.fail(failed);raise
        finally:_ACQUISITION.reset(token);self.token=None
    @property
    def has_output_or_error(self):return self.value is not None or self.error is not None
    def reserve(self,**amounts):
        lease=self.resources.reserve(**amounts);self.leases[id(lease)]=lease
        return lease
    def release(self,lease):
        self.resources._check_acquisitions()
        require(self.leases.get(id(lease)) is lease and lease is not self._bookkeeping and id(lease) not in self.resources._failed)
        lease.discard();del self.leases[id(lease)]
    def fail(self,error):
        self.resources.retain_failure(self._bookkeeping,error,acquisition=self)
        self.error=error
    def dispose(self):
        """Caller must first clear all untransferred aliases, including retained exceptions."""
        self.resources._check_acquisitions();self.resources.check_integrity()
        require(self.token is None and self.resources._acquisitions.get(id(self)) is self)
        for key in self.leases:self.resources._failed.pop(key,None)
        self.value=self.error=self.operation=None;self.args=();self.kwargs.clear()
        for use in self._source_uses.values():
            use.context=use.source=use.metadata=use.subject=use.value=use.error=None
        self._source_uses.clear()
        for decoded in self._owned_decodes.values():decoded.raw=decoded.value=decoded.witness=None
        self._owned_decodes.clear()
        while self.leases:
            key,lease=self.leases.popitem()
            lease.discard()
        del self.resources._acquisitions[id(self)]
    def decode(self,raw,loader,*,validate=None,comparison_budget=None):
        return self._decode(raw,loader,validate=validate,consume=False,comparison_budget=comparison_budget)
    def _decode_check(self,raw,loader,check):
        return self._decode(raw,loader,validate=check,consume=True)
    def _decode_owned(self,raw,loader,validate):
        return self._decode(raw,loader,validate=validate,consume=False,owned=True)
    def _release_owned(self,decoded):
        self._require_owned(decoded)
        decoded.raw=decoded.value=decoded.witness=None
        del self._owned_decodes[id(decoded)]
        self.release(decoded.validation);self.release(decoded.retained);self.release(decoded.registration)
    def _require_owned(self,decoded):
        self.resources._check_acquisitions();self.resources.check_integrity()
        require(type(decoded) is _OwnedDecode and decoded.owner is self
            and self._owned_decodes.get(id(decoded)) is decoded and self.error is None)
        require(type(decoded.witness) is tuple and len(decoded.witness)==3
            and decoded.witness[0] is self and decoded.witness[1] is decoded.validation
            and decoded.witness[2] is decoded.value)
        for lease in (decoded.retained,decoded.validation,decoded.registration):
            require(type(lease) is Reservation and self.leases.get(id(lease)) is lease
                and self.resources._leases.get(id(lease)) is lease and lease.owner is self.resources)
    def _require_validation(self,value):
        try:
            self.resources._check_acquisitions();self.resources.check_integrity()
            witness=_VALIDATION.get()
            require(self.resources.current_acquisition() is self and self.error is None
                and type(witness) is tuple and len(witness)==3 and witness[0] is self and witness[2] is value)
            lease=witness[1]
            require(type(lease) is Reservation and lease.owner is self.resources
                and self.leases.get(id(lease)) is lease and self.resources._leases.get(id(lease)) is lease)
        except Exhausted as error:error._vcw_resource_origin=self.resources;raise
    def _decode(self,raw,loader,*,validate,consume,owned=False,comparison_budget=None):
        if comparison_budget is not None:
            try:
                require(comparison_budget is self.resources.comparison and self.resources.current_acquisition() is self
                    and type(raw) in (str,bytes))
            except Exhausted as error:
                error._vcw_resource_origin=self.resources;self.fail(error);raise
        if owned:require(type(raw) in (str,bytes) and validate is not None and not consume)
        if type(raw) not in (str,bytes):return loader(raw)
        scan=self.reserve(requested=len(raw),live=4096+64*self.resources.depth_limit)
        text=raw;encoding=None;preparation=None;value=None
        try:
            if type(raw) is bytes:
                preparation=self.reserve(requested=len(raw),live=4*(len(raw)+1)+512)
                malformed=False
                try:
                    encoding=json.detect_encoding(raw);text=raw.decode(encoding,'surrogatepass')
                except UnicodeError:
                    malformed=True
                # Run the original loader outside the preparatory exception handler.
                if malformed:
                    text=encoding=None
                    units=chars=len(raw)
                else:chars=len(text)
            else:chars=len(text);malformed=False
            if not malformed:
                try:
                    chars,units,bmp,nonbmp,scalar_starts=_json_text_scan(text,
                        depth_limit=self.resources.depth_limit,unit_limit=self.resources.unit_limit)
                except Exhausted as error:error._vcw_resource_origin=self.resources;raise
            if comparison_budget is not None:
                comparison_material=(44*len(raw)+64 if malformed else
                    chars+5*bmp+11*nonbmp+32*scalar_starts+64)
                try:comparison_budget.take(bytes=comparison_material)
                except UseContractError as error:error._vcw_resource_origin=self.resources;raise
            text=encoding=None
            if preparation is not None:self.release(preparation);preparation=None
            tree=4*(chars+1)+384*(units+1)
            serialized=12*(chars+1)+4*(units+1)+32
            parser=3*tree+8*(chars+1)+512
            retained=self.reserve(live=40*(len(raw)+1)+384*(units+1)+512)
            parsing=self.reserve(requested=len(raw)+parser,live=parser)
            self.release(scan);scan=None
            # Original loaders retain syntax, duplicate, number and raw-BLOB semantics.
            value=loader(raw)
            self.release(parsing);parsing=None
            scratch=None
            if validate is not None:
                path=768+196*(min(chars,4096)+1)
                validation=4*tree+2*path+max(tree+3*serialized+2*tree,serialized+parser)
                work=6*tree+11*serialized+196*(chars+1)+964*(units+1)
                scratch=self.reserve(requested=work,live=validation)
                witness=(self,scratch,value)
                token=_VALIDATION.set(witness)
                try:checked=validate(value)
                finally:
                    _VALIDATION.reset(token)
                    token=None
                    if not owned:witness=None
                if consume:require(type(checked) is bool)
                checked_value=checked if consume else None
                checked=None;validate=None
                if not owned:self.release(scratch);scratch=None
            if owned:
                registration=self.reserve(requested=1024,live=1024)
                decoded=_OwnedDecode(self,raw,value,retained,scratch,registration,witness,tree,serialized)
                self._owned_decodes[id(decoded)]=decoded
                return decoded
            if consume:
                value=None;raw=None;loader=None
                self.release(retained)
                return checked_value
            return value
        except BaseException as error:self.fail(error);raise

class Exhausted(ValueError):pass

@dataclass
class _SourceUse:
    owner:object
    context:object
    source:object
    metadata:object
    subject:object
    registration:object
    value:object=None
    error:object=None
    def enter_context(self,lease):
        """Adopt only a real raw-read reservation; settlement is caller disposal."""
        owner=self.owner;resources=owner.resources
        resources._check_acquisitions();resources.check_integrity()
        require(resources.current_acquisition() is owner and owner._source_uses.get(id(self)) is self
            and type(lease) is Reservation and lease.owner is resources
            and resources._leases.get(id(lease)) is lease
            and all(record[1] is not lease for record in resources._connections.values())
            and all(record[1] is not lease for record in resources._exit_scopes.values())
            and all(id(lease) not in other.leases for other in resources._acquisitions.values()))
        owner.leases[id(lease)]=lease
        return lease.__enter__()

@dataclass
class _OwnedDecode:
    owner:object
    raw:object
    value:object
    retained:object
    validation:object
    registration:object
    witness:object
    tree:int
    serialized:int

def require(value):
    if not value:raise Exhausted('A0 resource precondition exhausted or changed')

def number(value):require(type(value) is int and value>=0)

@dataclass(frozen=True)
class Counter:
    limit:int
    initial:int=0
    state:dict=field(init=False,repr=False)
    def __post_init__(self):
        number(self.limit);number(self.initial);require(self.initial<=self.limit<=1<<30)
        object.__setattr__(self,'state',dict(used=self.initial,peak=self.initial))
    @property
    def used(self):return self.state['used']
    @property
    def peak(self):return self.state['peak']

@dataclass(frozen=True)
class Discovery:
    budget:dict
    _original:object=field(init=False,repr=False)
    _deadline:tuple=field(init=False,repr=False)
    _seen:tuple=field(init=False,repr=False)
    def __post_init__(self):
        require(type(self.budget) is dict)
        object.__setattr__(self,'_original',self.budget)
        object.__setattr__(self,'_deadline',('deadline' in self.budget,self.budget.get('deadline')))
        object.__setattr__(self,'_seen',(0,0))
        self.preflight(0,0)
    @property
    def state(self):return self.budget
    def preflight(self,paths,material):
        number(paths);number(material)
        require(self.budget is self._original and ('deadline' in self.budget,self.budget.get('deadline'))==self._deadline)
        startup._entry_budget_check(self.budget)
        current=(self.budget['paths'],self.budget['bytes'])
        require(all(value>=previous for value,previous in zip(current,self._seen)))
        object.__setattr__(self,'_seen',current)
        require(current[0]+paths<=4096 and current[1]+material<8<<20)
    def charge(self,*,paths=0,material=0):
        self.preflight(paths,material);self.state['paths']+=paths;self.state['bytes']+=material
        object.__setattr__(self,'_seen',(self.state['paths'],self.state['bytes']))
    def check_integrity(self):
        require(self.budget is self._original and ('deadline' in self.budget,self.budget.get('deadline'))==self._deadline)
        current=(self.budget['paths'],self.budget['bytes'])
        require(all(type(v) is int and v>=0 for v in current) and current[0]<=4096 and current[1]<8<<20)
        require(all(value>=previous for value,previous in zip(current,self._seen)))
        object.__setattr__(self,'_seen',current)

@dataclass(frozen=True)
class Reservation:
    owner:object
    amount:int
    def __enter__(self):
        self.owner.check();require(id(self) in self.owner._leases);return self
    def discard(self):self.owner._discard(self)
    def __exit__(self,*args):
        if id(self) in self.owner._leases:self.discard()

@dataclass(frozen=True)
class Decoded:
    owner:object
    lease:object
    value:object
    units:int
    material:int

def freeze(value):
    if type(value) is dict:return MappingProxyType({k:freeze(v) for k,v in value.items()})
    if type(value) is list:return tuple(map(freeze,value))
    return value

def thaw(value):
    if isinstance(value,MappingProxyType):return {k:thaw(v) for k,v in value.items()}
    if type(value) is tuple:return list(map(thaw,value))
    return value

def material(value):
    if isinstance(value,MappingProxyType):return 2+max(0,len(value)-1)+sum(material(k)+1+material(v) for k,v in value.items())
    if type(value) is tuple:return 2+max(0,len(value)-1)+sum(map(material,value))
    if type(value) is str:
        return 2+sum(2 if c in '\\"\b\f\n\r\t' else 1 if 32<=ord(c)<127 else 6 if ord(c)<=65535 else 12 for c in value)
    return len(json.dumps(value,ensure_ascii=True,allow_nan=False))

@dataclass(frozen=True)
class Resources:
    comparison:object
    discovery:Discovery
    work:Counter
    live:Counter
    unit_limit:int=65536
    depth_limit:int=64
    _leases:dict=field(default_factory=dict,init=False,repr=False)
    _connections:dict=field(default_factory=dict,init=False,repr=False)
    _tracking:dict=field(default_factory=dict,init=False,repr=False)
    _failed:dict=field(default_factory=dict,init=False,repr=False)
    _exit_scopes:dict=field(default_factory=dict,init=False,repr=False)
    _acquisitions:dict=field(default_factory=dict,init=False,repr=False)
    def __post_init__(self):
        require(isinstance(self.comparison,CaptureBudget) and type(self.discovery) is Discovery)
        require(type(self.work) is Counter and type(self.live) is Counter)
        require(self.work is not self.live and self.work.state is not self.live.state)
        require(self.work.limit<=1<<30 and self.live.limit<=64<<20)
        number(self.unit_limit);number(self.depth_limit);require(0<self.unit_limit<=65536 and 0<self.depth_limit<=64)
        require(set(self.comparison.limits)=={'parents','edges','jobs','bytes'})
        require(all(self.comparison.limits[k]<=(MAX_CAPTURE_BYTES if k=='bytes' else 4096) for k in self.comparison.limits))
        self._tracking.update(refs=(self.comparison.used,self.comparison.limits,self.discovery.state,self.work.state,self.live.state),
            limits=dict(self.comparison.limits),comparison=dict(self.comparison.used),discovery=dict(self.discovery.state),
            work=self.work.used,live=self.live.used,work_peak=self.work.peak,live_peak=self.live.peak,
            owners=(self.comparison,self.discovery,self.work,self.live),account_limits=(self.work.limit,self.live.limit,self.unit_limit,self.depth_limit),
            registries=(self._leases,self._connections,self._failed,self._exit_scopes,self._acquisitions),discovery_deadline=self.discovery._deadline)
        self.check()
    def check(self):
        try:self._check(prepaid=False)
        except (Exhausted,startup.StartupError) as error:error._vcw_resource_origin=self;raise
    def check_integrity(self):
        try:self._check(prepaid=True)
        except (Exhausted,startup.StartupError) as error:error._vcw_resource_origin=self;raise
    def _check(self,*,prepaid):
        t=self._tracking
        require(all(a is b for a,b in zip(t['owners'],(self.comparison,self.discovery,self.work,self.live))))
        require(t['account_limits']==(self.work.limit,self.live.limit,self.unit_limit,self.depth_limit))
        require(all(type(a) is dict and a is b for a,b in zip((self._leases,self._connections,self._failed,self._exit_scopes,self._acquisitions),t['registries'])))
        require(self.discovery._deadline==t['discovery_deadline'])
        require(all(a is b for a,b in zip(t['refs'],(self.comparison.used,self.comparison.limits,self.discovery.state,self.work.state,self.live.state))))
        require(self.comparison.limits==t['limits'] and all(type(v) is int for v in self.comparison.limits.values()))
        for account in (self.work,self.live):number(account.used);number(account.peak);require(account.used<=account.peak<=account.limit)
        require(all(type(self.comparison.used[k]) is int and t['comparison'][k]<=self.comparison.used[k]<=self.comparison.limits[k] for k in t['limits']))
        require(all(type(self.discovery.state[k]) is int and self.discovery.state[k]>=t['discovery'][k] for k in ('paths','bytes')))
        require(self.work.used==t['work'] and self.live.used==t['live'])
        require(type(self.work.peak) is int and type(self.live.peak) is int and self.work.peak>=t['work_peak'] and self.live.peak>=t['live_peak'])
        if prepaid:self.discovery.check_integrity()
        else:self.discovery.preflight(0,0)
        t['comparison'].update(self.comparison.used);t['discovery'].update(self.discovery.state)
    def reserve(self,*,requested=0,live=0,paths=0,discovery_material=0):
        try:return self._reserve(requested=requested,live=live,paths=paths,discovery_material=discovery_material)
        except (Exhausted,startup.StartupError) as error:error._vcw_resource_origin=self;raise
    def _reserve(self,*,requested=0,live=0,paths=0,discovery_material=0):
        self.check_reusable()
        for value in (requested,live,paths,discovery_material):number(value)
        self.discovery.preflight(paths,discovery_material)
        require(self.work.used+requested<=self.work.limit and self.live.used+live<=self.live.limit)
        lease=Reservation(self,live)
        self.discovery.charge(paths=paths,material=discovery_material)
        self.work.state['used']+=requested;self.live.state['used']+=live
        self.work.state['peak']=max(self.work.peak,self.work.used);self.live.state['peak']=max(self.live.peak,self.live.used)
        self._leases[id(lease)]=lease;self._tracking.update(work=self.work.used,live=self.live.used,work_peak=self.work.peak,live_peak=self.live.peak)
        self._tracking['discovery'].update(self.discovery.state)
        return lease
    def _discard(self,lease):
        self.check_integrity()
        require(type(lease) is Reservation and lease.owner is self and self._leases.get(id(lease)) is lease)
        self.live.state['used']-=self._leases.pop(id(lease)).amount;self._tracking['live']=self.live.used
    @contextmanager
    def read(self,expected,operation,*,limit=4<<20,hashing=False):
        number(expected);number(limit);require(expected<=limit<=4<<20)
        requested=expected+1;require(not hashing or requested<=65536)
        with self.reserve(requested=requested,live=requested):
            raw=operation(requested);self.check();require(type(raw) is bytes and len(raw)==expected);yield raw
    def _scan(self,raw):
        depth=units=0;quoted=escaped=scalar=False
        for char in raw:
            if quoted:
                if escaped:escaped=False
                elif char==92:escaped=True
                elif char==34:quoted=False
                continue
            if char==34:units+=1;quoted=True;scalar=False
            elif char in (123,91):units+=1;depth+=1;scalar=False
            elif char in (125,93):depth-=1;scalar=False
            elif char in (32,9,10,13,44,58):scalar=False
            elif not scalar:units+=1;scalar=True
            require(0<=depth<=self.depth_limit and units<=self.unit_limit)
        require(not quoted and not escaped and depth==0);return units
    @contextmanager
    def decode(self,raw,*,loader=json.loads):
        require(type(raw) is bytes and len(raw)<=4<<20)
        with self.reserve(live=512+64*self.depth_limit):units=self._scan(raw);self.check()
        # Accounted material only: raw/text/string upper bounds + pairs/dicts/frozen containers.
        estimate=10*(len(raw)+1)+384*units+512
        with self.reserve(live=estimate) as lease:
            def pairs(items):
                result={}
                for k,v in items:
                    if k in result:raise ValueError('Duplicate JSON key')
                    result[k]=v
                return result
            def finite(text):
                value=float(text)
                if not math.isfinite(value):raise ValueError('Nonfinite JSON number')
                return value
            def constant(text):raise ValueError('Nonfinite JSON constant')
            value=freeze(loader(raw.decode('utf-8'),object_pairs_hook=pairs,parse_float=finite,parse_constant=constant))
            self.check();yield Decoded(self,lease,value,units,material(value))
    @contextmanager
    def copy_fact(self,decoded,*,copier=thaw):
        self.check();require(type(decoded) is Decoded and decoded.owner is self and id(decoded.lease) in self._leases)
        require(self.comparison.used['bytes']+decoded.material<=self.comparison.limits['bytes'])
        with self.reserve(live=decoded.material+384*decoded.units):
            self.comparison.retain(decoded.value);value=copier(decoded.value);self.check();yield value
    def check_reusable(self):
        try:
            self._check_acquisitions();self.check()
            require(not self._failed and not self.has_unresolved_acquisition)
        except (Exhausted,startup.StartupError) as error:error._vcw_resource_origin=self;raise
    def _check_acquisitions(self):
        def require(value):
            if not value:
                error=Exhausted('Acquisition ownership changed');error._vcw_resource_origin=self;raise error
        require(type(self._leases) is dict and type(self._failed) is dict and type(self._acquisitions) is dict)
        def lease_at(key,lease):
            require(type(key) is int and key==id(lease) and type(lease) is Reservation and lease.owner is self
                and type(lease.amount) is int and lease.amount>=0 and self._leases.get(key) is lease)
        for key,lease in self._leases.items():lease_at(key,lease)
        for key,owner in self._acquisitions.items():
            require(type(key) is int and type(owner) is Acquisition and key==id(owner) and owner.resources is self and type(owner.leases) is dict)
            require(type(owner._source_uses) is dict)
            for use_key,use in owner._source_uses.items():
                require(type(use) is _SourceUse and use_key==id(use) and use.owner is owner)
                lease_at(id(use.registration),use.registration)
                require(owner.leases.get(id(use.registration)) is use.registration)
            require(type(owner._owned_decodes) is dict)
            for decoded_key,decoded in owner._owned_decodes.items():
                require(type(decoded) is _OwnedDecode and decoded_key==id(decoded) and decoded.owner is owner
                    and type(decoded.raw) in (str,bytes) and type(decoded.witness) is tuple and len(decoded.witness)==3
                    and decoded.witness[0] is owner and decoded.witness[1] is decoded.validation
                    and decoded.witness[2] is decoded.value)
                for lease in (decoded.retained,decoded.validation,decoded.registration):
                    lease_at(id(lease),lease)
                    require(owner.leases.get(id(lease)) is lease)
            bookkeeping=owner._bookkeeping
            require(type(bookkeeping) is Reservation and owner.leases.get(id(bookkeeping)) is bookkeeping and bookkeeping.amount==4096)
            failed=self._failed.get(id(bookkeeping))
            if owner.error is not None or failed is not None:
                require(isinstance(owner.error,BaseException) and type(failed) is tuple and len(failed)==2
                    and failed[0] is bookkeeping and failed[1] is owner.error)
        for owner in self._acquisitions.values():
            for lease_key,lease in owner.leases.items():
                lease_at(lease_key,lease)
                require(all(other is owner or lease_key not in other.leases for other in self._acquisitions.values()))
        for key,failed in self._failed.items():
            require(type(failed) is tuple and len(failed)==2 and isinstance(failed[1],BaseException))
            lease_at(key,failed[0])
    def retain_failure(self,lease,error,*,acquisition=None):
        self._check_acquisitions();self.check_integrity()
        require(type(lease) is Reservation and lease.owner is self and self._leases.get(id(lease)) is lease and isinstance(error,BaseException))
        owner=acquisition if acquisition is not None else _ACQUISITION.get()
        if type(owner) is Acquisition and owner.resources is self:
            require(self._acquisitions.get(id(owner)) is owner)
            require(all(other is owner or id(lease) not in other.leases for other in self._acquisitions.values()))
            owner.leases[id(lease)]=lease
        self._failed[id(lease)]=(lease,error)
    def acquisition(self):
        bookkeeping=self.reserve(live=4096)
        try:
            owner=Acquisition(self,bookkeeping);self._acquisitions[id(owner)]=owner
        except BaseException as error:self.retain_failure(bookkeeping,error);raise
        return owner
    def current_acquisition(self):
        owner=_ACQUISITION.get()
        try:require(type(owner) is Acquisition and owner.resources is self and self._acquisitions.get(id(owner)) is owner)
        except Exhausted as error:error._vcw_resource_origin=self;raise
        return owner
    @property
    def has_unresolved_acquisition(self):return any(owner.error is not None for owner in self._acquisitions.values())
    def _check_native_connection(self,connection):
        # Integrity only: mandatory prepaid exit must work after admission expires.
        try:
            require(type(connection) is sqlite3.Connection and connection.row_factory is None
                and connection.text_factory is str and type(sqlite3.adapters) is dict
                and (str,sqlite3.PrepareProtocol) not in sqlite3.adapters
                and (int,sqlite3.PrepareProtocol) not in sqlite3.adapters)
        except Exhausted as error:error._vcw_resource_origin=self;raise
    @contextmanager
    def registered_connection(self,connection):
        self._check_native_connection(connection);self.check_reusable()
        key=id(connection);require(key not in self._connections)
        lease=self.reserve(live=REGISTRATION_BYTES)
        record=None;installed=False
        try:
            record=(connection,lease)
            self._connections[key]=record;installed=True
            yield connection
        finally:
            intact=record is not None and self._connections.get(key) is record
            owned=self._leases.get(id(lease)) is lease
            try:
                if intact:del self._connections[key]
            finally:
                try:
                    if owned:lease.discard()
                finally:record=lease=None
            require((not installed or intact) and owned)
    @contextmanager
    def sql(self,connection,minimum,limit):
        self._check_native_connection(connection);self.check();record=self._connections.get(id(connection))
        require(type(connection) is sqlite3.Connection and type(record) is tuple and len(record)==2
            and record[0] is connection and type(record[1]) is Reservation and record[1].owner is self
            and record[1].amount==REGISTRATION_BYTES
            and self._leases.get(id(record[1])) is record[1] and connection.in_transaction)
        number(minimum);number(limit);require(limit<=4096)
        with self.reserve(requested=256,live=1024,paths=1):
            rows,blob,scalars,bad=connection.execute("""select count(*),coalesce(sum(length(cast(txt as blob))),0),
                coalesce(sum(length(cast(id as text))+coalesce(length(cast(n as text)),0)),0),
                coalesce(sum(typeof(txt) not in ('text','null') or typeof(id)!='integer' or typeof(n) not in ('integer','null')),0)
                from (select id,txt,n from payload where id>=? order by id limit ?)""",(minimum,limit+1)).fetchone()
            self.check();require(all(type(v) is int and v>=0 for v in (rows,blob,scalars,bad)) and not bad and rows<=limit)
        # BLOB length may be UTF-8 or UTF-16; 4*BLOB is an encoding-independent text upper bound, including NUL.
        requested=4*blob+scalars+24*rows
        with self.reserve(requested=requested,live=4*requested+256*rows,paths=rows,discovery_material=requested):
            yield dict(rows=rows,material=requested,accounting_only=True)
