"""Only the configured loopback receiver can be called by this pilot."""
import hashlib
import uuid
from urllib.parse import urlsplit
import httpx

class DeliveryError(RuntimeError):pass

class HttpDelivery:
    def __init__(self,url,token):
        parsed=urlsplit(url)
        if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','localhost','::1'} or parsed.username or parsed.password:
            raise ValueError('loopback_receiver_required')
        self.url=url;self.token=token
    def send(self,digest,scenario,text):
        key=str(uuid.uuid4())
        try:
            r=httpx.post(self.url,headers={'Authorization':'Bearer '+self.token},
                json={'id':key,'request_digest':digest,'scenario_id':scenario,'text':text},timeout=5,follow_redirects=False,trust_env=False)
            r.raise_for_status()
            result=r.json()
            if result.get('id')!=key or result.get('content_hash')!=hashlib.sha256(text.encode()).hexdigest():raise DeliveryError('receiver_receipt_mismatch')
            return result
        except (httpx.HTTPError,ValueError):
            # A timeout is delivery-unknown, never proof of non-delivery; do not retry.
            raise DeliveryError('delivery_outcome_unknown') from None
