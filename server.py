"""V47.6: local text-guided fashion detection via Grounding DINO Tiny.
Model weights download on first inference. CPU supported; CUDA used when available.
"""
import io
import os
import threading
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
MODEL_ID = os.getenv("FASHION_MODEL", "IDEA-Research/grounding-dino-tiny")
app = FastAPI(title="Fashion Lens V47.9")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
_detector = None
_device = None
_lock = threading.Lock()

# Independent descriptive prompts: text-guided model can find complete products
# rather than Fashionpedia garment parts.
PROMPTS = [
    ("Jacket / Coat", "jacket"), ("Jacket / Coat", "coat"),
    ("Jacket / Coat", "blazer"),
    ("Shirt / T-shirt", "t-shirt"), ("Shirt / T-shirt", "shirt"),
    ("Shirt / T-shirt", "top"), ("Shirt / T-shirt", "blouse"),
    ("Hoodie / Sweatshirt", "hoodie"), ("Hoodie / Sweatshirt", "sweater"),
    ("Jeans / Trousers", "pants"), ("Jeans / Trousers", "jeans"),
    ("Jeans / Trousers", "trousers"),
    ("Shorts", "shorts"), ("Skirt", "skirt"), ("Dress", "dress"),
    ("Sneakers / Shoes", "sneakers"), ("Sneakers / Shoes", "shoes"),
    ("Sneakers / Shoes", "boots"), ("Sneakers / Shoes", "sandals"),
    ("Bag", "handbag"), ("Bag", "bag"), ("Bag", "backpack"),
    ("Hat", "hat"), ("Glasses", "sunglasses"), ("Belt", "belt"),
    ("Watch", "wristwatch"), ("Watch", "watch"),
]
# Batches prompts into a single text phrase for Grounding DINO. Individual
# noun phrases separated by periods are supported by the model.
TEXT = " . ".join(dict.fromkeys(prompt for _, prompt in PROMPTS)) + " ."
MAP = {name:category for category,name in PROMPTS}

@app.get("/")
def home():
    return FileResponse(ROOT / "index.html")

@app.get("/health")
def health():
    return {"ok":True,"model":MODEL_ID,"loaded":_detector is not None,"device":_device}

def load_detector():
    global _detector, _device
    if _detector is None:
        with _lock:
            if _detector is None:
                import torch
                from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
                _device = "cuda" if torch.cuda.is_available() else "cpu"
                processor = AutoProcessor.from_pretrained(MODEL_ID)
                model = AutoModelForZeroShotObjectDetection.from_pretrained(MODEL_ID)
                model.to(_device).eval()
                _detector = (processor, model)
    return _detector

def overlap(a,b):
    x1=max(a[0],b[0]);y1=max(a[1],b[1]);x2=min(a[2],b[2]);y2=min(a[3],b[3])
    inter=max(0,x2-x1)*max(0,y2-y1)
    aa=max(0,a[2]-a[0])*max(0,a[3]-a[1])
    bb=max(0,b[2]-b[0])*max(0,b[3]-b[1])
    return inter/(aa+bb-inter) if aa+bb>inter else 0

def categorize(label):
    label=label.lower().strip().strip(". ")
    if label in MAP:return MAP[label]
    # Grounding DINO may output partial phrases, or a phrase with article.
    for phrase, category in sorted(MAP.items(),key=lambda x:-len(x[0])):
        if label == phrase or label.endswith(" "+phrase) or label.startswith(phrase+" "):
            return category
    return None

def containment(a,b):
    x1=max(a[0],b[0]);y1=max(a[1],b[1]);x2=min(a[2],b[2]);y2=min(a[3],b[3])
    intersection=max(0,x2-x1)*max(0,y2-y1)
    aa=max(1,(a[2]-a[0])*(a[3]-a[1]))
    bb=max(1,(b[2]-b[0])*(b[3]-b[1]))
    return intersection/min(aa,bb)

def area(b):
    return max(0,b[2]-b[0])*max(0,b[3]-b[1])

def center(b):
    return ((b[0]+b[2])/2,(b[1]+b[3])/2)

# Category-aware confidence floors prevent low-score accessory hallucinations.
FLOORS={"Glasses":.32,"Hat":.38,"Belt":.38,"Bag":.24,"Watch":.25,
        "Shorts":.30,"Dress":.29,"Skirt":.28,"Hoodie / Sweatshirt":.25,
        "Jacket / Coat":.20,"Shirt / T-shirt":.20,"Jeans / Trousers":.20,
        "Sneakers / Shoes":.24}
CLOTHING={"Jacket / Coat","Shirt / T-shirt","Hoodie / Sweatshirt","Dress"}

def select_products(candidates,w,h):
    # Cross-category comparisons use geometry AND confidence. Inner layers
    # are retained when their boxes occupy a substantially different region.
    priority={"Jacket / Coat":1.06,"Shirt / T-shirt":1.05,
              "Hoodie / Sweatshirt":.91,"Jeans / Trousers":1.08}
    ranked=sorted(candidates,key=lambda d:-(d["score"]*priority.get(d["label"],1)))
    selected=[]
    for obj in ranked:
        label=obj["label"];box=obj["box"]
        if obj["score"]<FLOORS.get(label,.24) or area(box)<w*h*.001:continue
        duplicate=False
        for prev in selected:
            pb=prev["box"];iou=overlap(box,pb);nested=containment(box,pb)
            ratio=min(area(box),area(pb))/max(area(box),area(pb),1)
            same=label==prev["label"]
            # Same garment, even if one detection is slightly smaller.
            if same and (iou>.38 or (nested>.92 and ratio>.42)):
                duplicate=True;break
            # Suppress alternative garment labels only for near-identical boxes.
            if label in CLOTHING and prev["label"] in CLOTHING:
                if ratio>.62 and (iou>.52 or nested>.88):
                    duplicate=True;break
            # Wrong labels (e.g. bag vs watch) occupying identical object region.
            if label not in CLOTHING and prev["label"] not in CLOTHING:
                if ratio>.65 and iou>.78:
                    duplicate=True;break
        if not duplicate:selected.append(obj.copy())
        if len(selected)>=24:break
    shoes=[o for o in selected if o["label"]=="Sneakers / Shoes"]
    used=set();groups=[]
    for a in shoes:
        if id(a) in used:continue
        best=None;best_distance=1e9
        for b in shoes:
            if a is b or id(b) in used:continue
            ab=a["box"];bb=b["box"]
            aw=ab[2]-ab[0];bw=bb[2]-bb[0];ah=ab[3]-ab[1];bh=bb[3]-bb[1]
            cy1=(ab[1]+ab[3])/2;cy2=(bb[1]+bb[3])/2
            cx1=(ab[0]+ab[2])/2;cx2=(bb[0]+bb[2])/2
            ratio=max(area(ab),area(bb))/max(1,min(area(ab),area(bb)))
            if ratio>2.8 or abs(cy1-cy2)>max(ah,bh)*.8:continue
            distance=abs(cx1-cx2)/max(aw,bw,1)
            if .25<distance<2.2 and distance<best_distance:best=b;best_distance=distance
        if best:
            used.add(id(a));used.add(id(best));boxes=[a["box"],best["box"]]
            groups.append({"label":"Sneakers / Shoes (Pair)","original_label":"paired shoes",
                "score":round((a["score"]+best["score"])/2,4),
                "box":[min(q[0] for q in boxes),min(q[1] for q in boxes),
                       max(q[2] for q in boxes),max(q[3] for q in boxes)],"regions":boxes})
    return sorted([o for o in selected if id(o) not in used]+groups,key=lambda x:-x["score"])

# Optional second-stage zero-shot crop verification. CLIP compares the actual
# detected crop against distinct product descriptions; it does not trust the
# original text-guided label unconditionally. Disabled with FASHION_VERIFY=0.
VERIFY_LABELS={
 "Jacket / Coat":"a photo of a jacket or coat outerwear garment",
 "Shirt / T-shirt":"a photo of a shirt or t-shirt garment",
 "Hoodie / Sweatshirt":"a photo of a sweatshirt or hoodie garment",
 "Jeans / Trousers":"a photo of a pair of pants or jeans trousers",
 "Shorts":"a photo of a pair of shorts",
 "Skirt":"a photo of a skirt",
 "Dress":"a photo of a dress",
 "Sneakers / Shoes":"a photo of sneakers or shoes",
 "Bag":"a photo of a handbag or backpack",
 "Glasses":"a photo of sunglasses or eyeglasses",
 "Hat":"a photo of a hat or cap",
 "Belt":"a photo of a belt",
 "Watch":"a photo of a wristwatch"
}
_verifier=None
_verify_lock=threading.Lock()
def load_verifier():
    global _verifier
    if _verifier is None:
        with _verify_lock:
            if _verifier is None:
                from transformers import CLIPModel, CLIPProcessor
                model_id=os.getenv("FASHION_VERIFY_MODEL","openai/clip-vit-base-patch32")
                processor=CLIPProcessor.from_pretrained(model_id)
                model=CLIPModel.from_pretrained(model_id).to(_device).eval()
                _verifier=(processor,model)
    return _verifier

def verify_candidates(image,candidates,torch):
    if os.getenv("FASHION_VERIFY","1")=="0":return candidates,0
    processor,model=load_verifier()
    # Check top candidates per spatial area; cap for CPU responsiveness.
    ranked=sorted(candidates,key=lambda x:-x["score"])[:55]
    labels=list(VERIFY_LABELS);texts=list(VERIFY_LABELS.values())
    verified=[];rejected=0
    for obj in ranked:
        x1,y1,x2,y2=[int(v) for v in obj["box"]]
        if x2-x1<10 or y2-y1<10:continue
        crop=image.crop((max(0,x1),max(0,y1),min(image.width,x2),min(image.height,y2)))
        if crop.width<5 or crop.height<5:continue
        inputs=processor(text=texts,images=crop,return_tensors="pt",padding=True).to(_device)
        with torch.inference_mode():
            scores=model(**inputs).logits_per_image.softmax(dim=-1)[0].tolist()
        ranked_idx=sorted(range(len(scores)),key=lambda i:-scores[i])
        top=labels[ranked_idx[0]];top_score=scores[ranked_idx[0]]
        original=obj["label"];original_score=scores[labels.index(original)]
        updated=obj.copy()
        updated["verification"]={"best_category":top,"best_probability":round(top_score,3),
                                 "original_probability":round(original_score,3)}
        # Only relabel with meaningful CLIP margin. Don't turn partially
        # visible layered clothes into other categories based on tiny margins.
        if top!=original and top_score>original_score*1.65 and top_score>.24:
            updated["label"]=top
        # Accessories need independent visual confirmation.
        if original in {"Glasses","Hat","Belt","Bag","Watch"} and top!=original and original_score<.075:
            rejected+=1;continue
        verified.append(updated)
    return verified,rejected

# Targeted second pass: an inner top may be missed in the full collage.
# Crop to the left-hand outfit region, and ask specifically about upper garments.
SECOND_TEXT="jacket . coat . shirt . t-shirt . turtleneck shirt . sweater . sweatshirt ."
SECOND_MAP={"turtleneck shirt":"Shirt / T-shirt","sweatshirt":"Hoodie / Sweatshirt"}

def inference(image,processor,model,torch,text,offset=(0,0),score_threshold=.06):
    w,h=image.size
    inputs=processor(images=image,text=text,return_tensors="pt").to(_device)
    with torch.inference_mode():outputs=model(**inputs)
    result=processor.post_process_grounded_object_detection(
        outputs,inputs.input_ids,threshold=score_threshold,text_threshold=.15,
        target_sizes=[(h,w)])[0]
    predictions=[]
    for score,label,box in zip(result["scores"],result["labels"],result["boxes"]):
        raw=str(label).lower().strip().strip('. ')
        category=SECOND_MAP.get(raw) or categorize(raw)
        x1,y1,x2,y2=[float(v) for v in box.tolist()]
        coords=[max(0,x1)+offset[0],max(0,y1)+offset[1],min(w,x2)+offset[0],min(h,y2)+offset[1]]
        predictions.append({"label":category,"original_label":raw,"score":round(float(score),4),"box":coords})
    return predictions

@app.post("/detect")
async def detect(file:UploadFile=File(...)):
    if file.content_type and not file.content_type.startswith("image/"):
        raise HTTPException(415,"Please upload an image.")
    content=await file.read(12*1024*1024+1)
    if len(content)>12*1024*1024:raise HTTPException(413,"Max image size 12 MB.")
    try:
        image=ImageOps.exif_transpose(Image.open(io.BytesIO(content))).convert("RGB")
        w,h=image.size
        if w*h>30_000_000:raise ValueError("Image too large.")
    except Exception as exc:
        raise HTTPException(400,f"Invalid image: {exc}") from exc
    try:
        import torch
        processor,model=load_detector()
        first=inference(image,processor,model,torch,TEXT)
        # Scan left portion independently for layers hidden by outerwear.
        # This is intentionally conservative; weak overlapping guesses are
        # retained in diagnostics, not forced into product cards.
        x_end=max(1,int(w*.64))
        second=inference(image.crop((0,0,x_end,h)),processor,model,torch,
                         SECOND_TEXT,score_threshold=.12)
        predictions=first+second
        candidates=[];diagnostics=[]
        for obj in predictions:
            category=obj["label"]
            diagnostics.append({"label":obj["original_label"],"category":category,
                "reason":"product" if category else "unmapped label",
                "score":obj["score"],"box":obj["box"]})
            if category and area(obj["box"])>0:candidates.append(obj)
        verified,rejected=verify_candidates(image,candidates,torch)
        items=select_products(verified,w,h)
        return {"items":items,"raw_detections":sorted(diagnostics,key=lambda d:-d["score"])[:100],
            "diagnostics":{"raw":len(diagnostics),"product_candidates":len(candidates),
                "component":0,"unmapped":len(diagnostics)-len(candidates),
                "not_clothing":0,"after_deduplication":len(items),
                "removed_by_deduplication":len(candidates)-len(items),
                "verification_rejected":rejected,"verification_enabled":os.getenv("FASHION_VERIFY","1")!="0"},
            "width":w,"height":h,"model":MODEL_ID,"device":_device,"passes":2,"version":"50.3"}
    except Exception as exc:
        raise HTTPException(503,f"AI inference failed: {type(exc).__name__}: {exc}") from exc




# Local-only one-shot image handoff to the companion extension.
# No image is uploaded to third-party services by the Python server.
import base64
import secrets
import time
from fastapi import Request
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8000", "http://localhost:8000", "chrome-extension://*"], allow_methods=["GET", "POST"], allow_headers=["*"])
_lens_jobs = {}
class LensPayload(BaseModel):
    image: str
    name: str = "Fashion product"

@app.post("/lens-bridge/queue")
def queue_lens(payload: LensPayload, request: Request):
    if request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "Local requests only")
    try:
        header, encoded = payload.image.split(",", 1)
        if header != "data:image/jpeg;base64": raise ValueError()
        raw = base64.b64decode(encoded, validate=True)
        if not (100 <= len(raw) <= 5_000_000): raise ValueError()
        with Image.open(io.BytesIO(raw)) as im: im.verify()
    except Exception:
        raise HTTPException(400, "Invalid JPEG crop")
    now = time.time()
    for key, value in list(_lens_jobs.items()):
        if now - value["time"] > 120: _lens_jobs.pop(key, None)
    token = secrets.token_urlsafe(18)
    _lens_jobs[token] = {"time": now, "image": payload.image, "name": payload.name[:120]}
    return {"queued": True, "id": token}

@app.get("/lens-bridge/next")
def next_lens(request: Request):
    if request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "Local requests only")
    now = time.time()
    for key, value in list(_lens_jobs.items()):
        _lens_jobs.pop(key, None)
        if now - value["time"] < 120:
            return {"id": key, "image": value["image"], "name": value["name"]}
    return {"job": None}


# V49.8 best-effort public product metadata. No fabricated prices.
@app.get('/product-info')
async def product_info(url: str):
    import asyncio, ipaddress, json, re, socket
    from html.parser import HTMLParser
    from urllib.parse import urlparse
    import httpx
    from fastapi import HTTPException
    parsed=urlparse(url)
    if parsed.scheme not in ('https','http') or not parsed.hostname:
        raise HTTPException(400,'Invalid product URL')
    host=parsed.hostname.lower().rstrip('.')
    if host in ('localhost',) or host.endswith(('.local','.internal')):
        raise HTTPException(400,'Private hosts are not allowed')
    def check_host(h):
        try:
            records=socket.getaddrinfo(h,None,type=socket.SOCK_STREAM)
            if not records or any(not ipaddress.ip_address(record[4][0]).is_global for record in records):
                raise ValueError('Private address')
        except (OSError,ValueError):
            raise HTTPException(400,'Host cannot be fetched safely')
    await asyncio.to_thread(check_host,host)
    class Metadata(HTMLParser):
        def __init__(self):super().__init__();self.meta={};self.scripts=[];self.inscript=False;self.buf=[]
        def handle_starttag(self,tag,attrs):
            a=dict(attrs)
            if tag=='meta':
                k=(a.get('property') or a.get('name') or '').lower()
                if k and a.get('content'):self.meta[k]=a['content']
            if tag=='script' and a.get('type','').lower()=='application/ld+json':self.inscript=True;self.buf=[]
        def handle_data(self,d):
            if self.inscript:self.buf.append(d)
        def handle_endtag(self,tag):
            if tag=='script' and self.inscript:self.scripts.append(''.join(self.buf));self.inscript=False
    try:
        async with httpx.AsyncClient(timeout=9,follow_redirects=False,headers={'User-Agent':'Mozilla/5.0 (compatible; FashionMediaCollector/49.8)','Accept':'text/html'}) as client:
            response=await client.get(url)
        if 300<=response.status_code<400:
            raise HTTPException(422,'Store redirected the request; paste its final product URL')
        response.raise_for_status()
        if 'text/html' not in response.headers.get('content-type',''):
            raise HTTPException(422,'Product page is not HTML')
        doc=Metadata();doc.feed(response.text[:1200000]);meta=doc.meta
        title=meta.get('og:title') or meta.get('twitter:title') or ''
        currency=meta.get('product:price:currency') or meta.get('og:price:currency') or ''
        amount=meta.get('product:price:amount') or meta.get('og:price:amount')
        def visit(obj):
            nonlocal title,currency,amount
            if isinstance(obj,list):
                for x in obj:visit(x)
            elif isinstance(obj,dict):
                typ=obj.get('@type',[]);typ=[typ] if isinstance(typ,str) else typ
                if 'Product' in typ:
                    title=title or obj.get('name','')
                    offers=obj.get('offers',{})
                    if isinstance(offers,list):offers=next((o for o in offers if isinstance(o,dict)),{})
                    if isinstance(offers,dict):
                        amount=amount or offers.get('price') or offers.get('lowPrice')
                        currency=currency or offers.get('priceCurrency','')
                for k in ('@graph','mainEntity'):
                    if k in obj:visit(obj[k])
        for script in doc.scripts[:25]:
            try:visit(json.loads(script))
            except (ValueError,TypeError):pass
        try:
            numeric=float(str(amount).replace(',','').strip()) if amount is not None else None
            if numeric is not None and (numeric<=0 or numeric>100000000):numeric=None
        except (TypeError,ValueError):numeric=None
        return {'title':str(title)[:200],'price':numeric,'currency':str(currency).upper()[:5] or None,'store':host,'available':numeric is not None}
    except HTTPException:raise
    except (httpx.HTTPError,UnicodeError,ValueError):
        return {'title':'','price':None,'currency':None,'store':host,'available':False}


# V49.9: only data captured from a shopping product page open in the user's Chrome.
# Kept in local process memory, not transmitted to third-party services.
_store_prices = {}

@app.post('/store-price-capture')
async def store_price_capture(payload: dict):
    from urllib.parse import urlsplit
    import time
    url=str(payload.get('url',''))[:2500]
    parsed=urlsplit(url)
    host=(parsed.hostname or '').lower()
    allowed=('myntra.com','ajio.com','flipkart.com','amazon.in')
    if parsed.scheme!='https' or not any(host==h or host.endswith('.'+h) for h in allowed):
        raise HTTPException(400,'Unsupported store URL')
    try: price=float(payload['price'])
    except (TypeError,ValueError,KeyError):raise HTTPException(400,'Invalid price')
    if not 0<price<100000000 or payload.get('currency')!='INR':raise HTTPException(400,'Invalid INR price')
    key=parsed.scheme+'://'+host+parsed.path.rstrip('/')
    _store_prices[key]={'price':price,'currency':'INR','title':str(payload.get('title',''))[:240],
                        'store':host,'source':str(payload.get('source','visible'))[:50], 'time':time.time()}
    if len(_store_prices)>300:
        for k in sorted(_store_prices,key=lambda x:_store_prices[x]['time'])[:100]:_store_prices.pop(k,None)
    return {'ok':True}

@app.get('/store-price-capture')
async def get_store_price_capture(url: str):
    from urllib.parse import urlsplit
    import time
    parsed=urlsplit(url)
    key=parsed.scheme+'://'+(parsed.hostname or '').lower()+parsed.path.rstrip('/')
    item=_store_prices.get(key)
    if not item or time.time()-item['time']>7200:return {'available':False}
    return {**item,'available':True}


# V50: locally stored temporary imports, delivered only to the local detector UI.
import base64 as _v50_base64
import uuid as _v50_uuid
from fastapi import HTTPException as _v50_HTTPException
from fastapi.responses import Response as _v50_Response
_v50_imports = {}

@app.post('/import-image')
async def v50_import_image(payload: dict):
    data = str(payload.get('image') or '')
    if not data.startswith('data:image/') or ';base64,' not in data:
        raise _v50_HTTPException(400, 'Expected base64 image')
    mime, encoded = data.split(';base64,', 1)
    mime = mime[5:]
    if mime not in ('image/png','image/jpeg','image/webp','image/gif'):
        raise _v50_HTTPException(400, 'Unsupported image type')
    if len(encoded) > 17_000_000:
        raise _v50_HTTPException(413, 'Image too large')
    try:
        content = _v50_base64.b64decode(encoded, validate=True)
    except Exception:
        raise _v50_HTTPException(400, 'Invalid image encoding')
    if len(content) > 12*1024*1024:
        raise _v50_HTTPException(413, 'Image too large')
    ident = _v50_uuid.uuid4().hex
    _v50_imports.clear()  # only one pending image, avoid accumulating sensitive images
    _v50_imports[ident] = (mime, content)
    return {'ok': True, 'id': ident}

@app.get('/import-image/{ident}')
async def v50_get_import(ident: str):
    item = _v50_imports.pop(ident, None)
    if not item:
        raise _v50_HTTPException(404, 'Image no longer available')
    return _v50_Response(item[1], media_type=item[0], headers={'Cache-Control':'no-store'})
