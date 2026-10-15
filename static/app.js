// V50.2: image-to-outfit association exists only for extension-launched imports.
let collectorOutfitKey='';
function notifyOutfit(){
 if(!collectorOutfitKey||!data)return;
 const products=items.map((p,i)=>({index:p.manual?100+i:p._id,name:p.label,score:p.score,x:((p.box[0]+p.box[2])/2/data.width)*100,y:((p.box[1]+p.box[3])/2/data.height)*100}));
 const links=savedLinks.map(l=>({...l,index:String(l.productId||'').startsWith('manual:')?100+items.findIndex(p=>p.manual&&'manual:'+p.label===l.productId):Number(l.productId)})).filter(l=>Number.isInteger(l.index)&&l.index>=0);
 window.postMessage({source:'FMC_LOCAL_DETECTOR',type:'OUTFIT_SYNC',outfitKey:collectorOutfitKey,products,links},location.origin);
}
window.addEventListener('message',e=>{if(e.origin===location.origin&&e.data?.source==='FMC_LENS_COMPANION'&&e.data.type==='OUTFIT_SYNC_ACK'&&!e.data.ok){console.warn('Outfit sync:',e.data.error)}});

const $=id=>document.getElementById(id);
let file=null,data=null,items=[],active=-1,loading=false,manualMode=false,manualItems=[],removedIds=new Set(),editedNames=new Map(),editingBoxIndex=-1;
// V49.8: saved links are scoped to the SHA-256 of the uploaded image bytes.
let imageId=null, imageChangeToken=0, savedLinks=[];
const LINKS_KEY='fmc498_image_links';
function allImageLinks(){try{return JSON.parse(localStorage.getItem(LINKS_KEY)||'{}')}catch{return {}}}
function persistLinks(){if(!imageId)return;const all=allImageLinks();all[imageId]=savedLinks;localStorage.setItem(LINKS_KEY,JSON.stringify(all));notifyOutfit()}
async function loadImageLinks(f,token){
 const hash=await crypto.subtle.digest('SHA-256',await f.arrayBuffer());
 if(token!==imageChangeToken)return;
 imageId=Array.from(new Uint8Array(hash),x=>x.toString(16).padStart(2,'0')).join('');
 savedLinks=Array.isArray(allImageLinks()[imageId])?allImageLinks()[imageId]:[];
 render();
}

function safeText(s){return String(s||"").replace(/[\u0000-\u001f]/g,"").trim()}
// Visual search uses the actual product crop, never generic keyword shopping searches.
let lensBusy=false;
async function cropBlob(item){
 if(!data||!img.complete||!img.naturalWidth)throw Error('Upload an image first.');
 const [x1,y1,x2,y2]=item.box;const scaleX=img.naturalWidth/data.width,scaleY=img.naturalHeight/data.height;
 const pad=Math.max(4,Math.round(Math.max(x2-x1,y2-y1)*.04));
 const x=Math.max(0,Math.floor((x1-pad)*scaleX)),y=Math.max(0,Math.floor((y1-pad)*scaleY));
 const right=Math.min(img.naturalWidth,Math.ceil((x2+pad)*scaleX)),bottom=Math.min(img.naturalHeight,Math.ceil((y2+pad)*scaleY));
 const c=document.createElement('canvas');c.width=right-x;c.height=bottom-y;
 if(c.width<2||c.height<2)throw Error('Invalid crop box.');
 c.getContext('2d').drawImage(img,x,y,c.width,c.height,0,0,c.width,c.height);
 return await new Promise((resolve,reject)=>c.toBlob(b=>b?resolve(b):reject(Error('Crop failed.')),'image/jpeg',.94));
}
async function visualSearch(item){
 if(lensBusy)return;
 lensBusy=true;
 try{
  status('Cropping product for Chrome image search…');
  const blob=await cropBlob(item);
  const dataUrl=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('Unable to read crop'));reader.readAsDataURL(blob)});
  const res=await fetch('/lens-bridge/queue',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image:dataUrl,name:item.label})});
  if(!res.ok)throw Error('Local bridge rejected crop');
  const job=await res.json();
  // Companion content script delivers this event to the extension service worker immediately.
  window.postMessage({source:'FMC_LOCAL_DETECTOR',type:'LENS_CROP_READY',id:job.id},location.origin);
  status('Opening Google Search and copying your cropped product automatically…');
 }catch(err){status('Lens bridge failed: '+err.message+'. Use Crop to save the image manually.',true)}finally{lensBusy=false}
}

function makeButton(label,fn){const b=document.createElement("button");b.type="button";b.textContent=label;b.onclick=fn;return b}

const img=$('photo');
const status=(msg,error=false)=>{$('status').textContent=msg;$('status').classList.toggle('error',error)};
$('upload').onchange=()=>{if(!window.fmcImportLoading)collectorOutfitKey='';const token=++imageChangeToken;imageId=null;savedLinks=[];file=$('upload').files[0];if(!file)return;if(!file.type.startsWith('image/'))return status('Choose an image.',true);if(file.size>12*1024*1024)return status('Max image size is 12 MB.',true);if(img.dataset.url)URL.revokeObjectURL(img.dataset.url);img.dataset.url=URL.createObjectURL(file);img.src=img.dataset.url;img.hidden=false;$('placeholder').hidden=true;$('canvasWrap').classList.add('has-image');data=null;items=[];manualItems=[];removedIds.clear();editedNames.clear();active=-1;showDiagnostics();$('detect').disabled=false;$('manual').disabled=false;status('Image ready. Detect products or add one manually.');render();loadImageLinks(file,token).catch(()=>status('Unable to restore links for this image.',true))};
$('threshold').oninput=()=>{$('thresholdValue').textContent=$('threshold').value+'%';if(data)filter()};
function filter(){if(!data)return;const min=Number($('threshold').value)/100;items=[...data.items.map((x,i)=>({...x,_id:i,box:[...x.box],label:editedNames.get(i)||x.label})).filter(x=>x.score>=min&&!removedIds.has(x._id)),...manualItems];active=-1;render();notifyOutfit();status(`${items.length} products shown. ${data.diagnostics ? data.diagnostics.raw + ' raw predictions, ' + data.diagnostics.product_candidates + ' product candidates.' : ''}`);showDiagnostics()}
function icon(s){s=s.toLowerCase();return /shoe|sneaker|boot|sandal/.test(s)?'👟':/bag|wallet/.test(s)?'👜':/jean|trouser|pant|short|skirt/.test(s)?'👖':/dress/.test(s)?'👗':/hat|cap/.test(s)?'🧢':/watch/.test(s)?'⌚':/glass/.test(s)?'🕶️':'👕'}
function clamp(v,min,max){return Math.min(max,Math.max(min,v))}
function render(){const results=$('results'),boxes=$('boxes');results.replaceChildren();boxes.replaceChildren();$('count').textContent=items.length+' item'+(items.length===1?'':'s');$('export').disabled=!items.length;
if(!items.length){const el=document.createElement('div');el.className='empty-results';el.innerHTML='<span>✨</span><b>No products shown</b><span>Scan an image or add an item manually.</span>';results.append(el);return}
const im=img.getBoundingClientRect(),area=$('canvasWrap').getBoundingClientRect(),dx=im.left-area.left,dy=im.top-area.top;
items.forEach((item,i)=>{const [x1,y1,x2,y2]=item.box;const b=document.createElement('button');b.type='button';b.className='hotspot'+(active===i?' active':'');b.textContent=i+1;b.title=`${item.label} · ${Math.round(item.score*100)}%`;b.style.left=(dx+(x1+x2)/2/data.width*im.width)+'px';b.style.top=(dy+(y1+y2)/2/data.height*im.height)+'px';b.onclick=()=>{active=i;render();visualSearch(items[i])};boxes.append(b);
const row=document.createElement('article');row.className='result'+(active===i?' active':'');const head=document.createElement('div');head.className='item-head';const badge=document.createElement('span');badge.className='item-number';badge.textContent=i+1;const ico=document.createElement('span');ico.textContent=icon(item.label);const input=document.createElement('input');input.value=item.label;input.setAttribute('aria-label','Edit product name');input.onchange=()=>{item.label=input.value.trim()||item.label;if(!item.manual)editedNames.set(item._id,item.label);render()};const confidence=document.createElement('small');confidence.textContent=item.manual?'Manual':Math.round(item.score*100)+'% confidence';head.append(badge,ico,input,confidence);
const actions=document.createElement('div');actions.className='item-actions';
const mk=(label,fn,cls='')=>{const btn=makeButton(label,fn);if(cls)btn.className=cls;actions.append(btn)};
// Clicking the numbered hotspot or product header starts Lens immediately.

mk('↓ Crop',()=>crop(item,true));
mk('✎ Edit box',()=>{editingBoxIndex=i;manualMode=true;wrap.classList.add('drawing');$('manual').textContent='Cancel drawing';status('Draw a new rectangle for this product.');});
mk('×',()=>{items.splice(i,1);if(item.manual)manualItems=manualItems.filter(x=>x!==item);else removedIds.add(item._id);active=-1;render()},'remove-button');
const shop=document.createElement('div');shop.className='shopping-box';
const preview=document.createElement('div');preview.className='item-preview';
const thumb=document.createElement('canvas');thumb.width=90;thumb.height=90;thumb.title='Click crop to search with Chrome';thumb.style.cursor='pointer';preview.append(thumb);
const info=document.createElement('div');info.innerHTML='<strong>Search using this exact image</strong><span>Use Chrome’s native image search. Exact retail matches are not guaranteed.</span>';preview.append(info);shop.append(preview);
if(data&&img.complete&&img.naturalWidth){const [x1,y1,x2,y2]=item.box;const sx=img.naturalWidth/data.width,sy=img.naturalHeight/data.height;const ctx=thumb.getContext('2d');ctx.fillStyle='#f4f7fb';ctx.fillRect(0,0,90,90);const w=(x2-x1)*sx,h=(y2-y1)*sy;const scale=Math.min(90/Math.max(1,w),90/Math.max(1,h));try{ctx.drawImage(img,x1*sx,y1*sy,w,h,(90-w*scale)/2,(90-h*scale)/2,w*scale,h*scale)}catch{}}
const matching=savedLinks.filter(x=>x.productId===(item.manual?'manual:'+item.label:String(item._id)) || (!x.productId && x.name===item.label));
const entry=document.createElement('div');entry.className='link-entry';
const url=document.createElement('input');url.placeholder='Save verified product URL';url.type='url';url.setAttribute('aria-label','Product link');
const price=document.createElement('input');price.placeholder='Price ₹ (auto if available)';price.type='number';price.min='0';price.setAttribute('aria-label','Price in INR');
const lookupNote=document.createElement('small');lookupNote.className='price-note';lookupNote.textContent='Paste a link to look up its price.';
let lookupSeq=0,metadata=null;
async function lookupPrice(){
 const candidate=url.value.trim();const seq=++lookupSeq;metadata=null;price.value='';
 if(!/^https?:\/\//i.test(candidate)){lookupNote.textContent='Enter a shopping link to fetch the price.';return}
 lookupNote.textContent='Checking Chrome-captured price and store metadata…';
 try{let response=await fetch('/store-price-capture?url='+encodeURIComponent(candidate));let result=await response.json();if(seq!==lookupSeq)return;
  if(!result.available){response=await fetch('/product-info?url='+encodeURIComponent(candidate));result=await response.json();}if(seq!==lookupSeq)return;
  if(!response.ok)throw Error(result.detail||'Lookup failed');
  metadata=result;
  if(result.price!==null && result.currency==='INR'){price.value=result.price;lookupNote.textContent=(result.source?'✓ Chrome page price: ₹':'✓ Store price: ₹')+result.price+(result.title?' · '+result.title.slice(0,48):'');}
  else lookupNote.textContent=result.currency&&result.currency!=='INR'?'Store currency is '+result.currency+'; enter INR price manually.':'Price not captured yet. Open the product in Chrome, wait a few seconds, then paste the link again (or enter price manually).';
 }catch(err){if(seq===lookupSeq)lookupNote.textContent='Could not fetch price; enter it manually.';}
}
let autoSaveTimer=null;
async function autoSaveLink(){
 const candidate=url.value.trim();if(!/^https?:\/\//i.test(candidate)||!imageId)return;
 await lookupPrice();
 if(url.value.trim()!==candidate)return;
 let parsed;try{parsed=new URL(candidate);if(!['http:','https:'].includes(parsed.protocol))return}catch{return}
 const amount=price.value.trim()?Number(price.value):null;
 const productId=item.manual?'manual:'+item.label:String(item._id);
 const record={productId,name:item.label,url:parsed.href,price:amount,title:metadata?.title||'',currency:'INR',savedAt:new Date().toISOString()};
 const oldIndex=savedLinks.findIndex(x=>x.productId===productId&&x.url===parsed.href);
 if(oldIndex>=0)savedLinks[oldIndex]={...savedLinks[oldIndex],...record};else savedLinks.push(record);
 persistLinks();status('Shopping link saved automatically to this outfit.');render();
}
function queueAutoSave(){clearTimeout(autoSaveTimer);autoSaveTimer=setTimeout(()=>autoSaveLink().catch(e=>status('Link auto-save failed: '+e.message,true)),650)}
url.addEventListener('input',queueAutoSave);url.addEventListener('paste',queueAutoSave);
url.addEventListener('change',queueAutoSave);
entry.append(url,price,makeButton('Save',()=>{let parsed;try{parsed=new URL(url.value.trim());if(!['https:','http:'].includes(parsed.protocol))throw Error('protocol');}catch{status('Enter a valid http(s) product URL.',true);return;}
if(!imageId){status('Please wait for image identification before saving.',true);return;}
const amount=price.value.trim()?Number(price.value):null;
savedLinks.push({productId:item.manual?'manual:'+item.label:String(item._id),name:item.label,url:parsed.href,price:amount,title:metadata?.title||'',currency:'INR',savedAt:new Date().toISOString()});persistLinks();render();status(amount===null?'Link saved; price not available automatically.':'Shopping link and price saved.');}));shop.append(entry,lookupNote);
for(const link of matching){const line=document.createElement('div');line.className='saved-link';const a=document.createElement('a');a.href=link.url;a.target='_blank';a.rel='noopener noreferrer';a.textContent=new URL(link.url).hostname+(link.price!==null?' · ₹'+link.price:'');line.append(a,makeButton('×',()=>{const idx=savedLinks.indexOf(link);if(idx>=0)savedLinks.splice(idx,1);persistLinks();render()}));shop.append(line)}
row.append(head,actions,shop);row.onclick=e=>{if(e.target===row||e.target===head||e.target===badge||e.target===ico||e.target===thumb){active=i;render();visualSearch(items[i])}};results.append(row)})}
function crop(item,download){if(!data||!img.complete)return;if(download)cropBlob(item).then(blob=>{if(!blob)return status('Could not crop this image.',true);const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=(item.label.replace(/[^a-z0-9]+/gi,'-')||'fashion-product')+'.jpg';a.click();setTimeout(()=>URL.revokeObjectURL(url),3000)}).catch(e=>status(e.message,true))}
img.onload=render;window.onresize=render;
$('detect').onclick=async()=>{if(!file||loading)return;loading=true;$('detect').disabled=true;status('Running fashion AI… First scan downloads the model.');try{const f=new FormData();f.append('file',file);const r=await fetch('/detect',{method:'POST',body:f});const payload=await r.json();if(!r.ok)throw Error(payload.detail||'Detection failed');if(!Array.isArray(payload.items))throw Error('Unexpected response');data=payload;removedIds.clear();editedNames.clear();filter();if(!items.length)status('No products displayed. Open Raw AI predictions below to see what the model recognized. Try confidence 5%.')}catch(e){status('Detection failed: '+e.message,true)}finally{loading=false;$('detect').disabled=false}};

// Draw a rectangle directly over the uploaded photo to add a missed product.
const wrap=$('canvasWrap');
let drawStart=null,drawRect=null;
function pixelPosition(event){
  const bounds=img.getBoundingClientRect();
  const x=clamp((event.clientX-bounds.left)/bounds.width,0,1)*data.width;
  const y=clamp((event.clientY-bounds.top)/bounds.height,0,1)*data.height;
  return [x,y];
}
$('manual').onclick=()=>{
  if(!img.complete||!img.naturalWidth)return;
  if(!data)data={width:img.naturalWidth,height:img.naturalHeight,items:[]};
  manualMode=!manualMode;if(!manualMode)editingBoxIndex=-1;
  wrap.classList.toggle('drawing',manualMode);
  $('manual').textContent=manualMode?'✕ Cancel draw':'+ Draw item';
  status(manualMode?'Drag a rectangle around the missing product on the image.':'Drawing cancelled.');
};
wrap.addEventListener('pointerdown',e=>{
  if(!manualMode||e.target.closest('.hotspot'))return;
  e.preventDefault();
  drawStart=pixelPosition(e);
  drawRect=document.createElement('div');
  drawRect.className='drawing-box';
  wrap.append(drawRect);
  wrap.setPointerCapture(e.pointerId);
});
wrap.addEventListener('pointermove',e=>{
  if(!drawStart||!drawRect)return;
  const now=pixelPosition(e);
  const im=img.getBoundingClientRect(),area=wrap.getBoundingClientRect();
  const x1=Math.min(now[0],drawStart[0]),x2=Math.max(now[0],drawStart[0]);
  const y1=Math.min(now[1],drawStart[1]),y2=Math.max(now[1],drawStart[1]);
  drawRect.style.left=(im.left-area.left+x1/data.width*im.width)+'px';
  drawRect.style.top=(im.top-area.top+y1/data.height*im.height)+'px';
  drawRect.style.width=((x2-x1)/data.width*im.width)+'px';
  drawRect.style.height=((y2-y1)/data.height*im.height)+'px';
});
wrap.addEventListener('pointerup',e=>{
  if(!drawStart)return;
  const end=pixelPosition(e);
  const box=[Math.min(drawStart[0],end[0]),Math.min(drawStart[1],end[1]),
             Math.max(drawStart[0],end[0]),Math.max(drawStart[1],end[1])];
  drawRect?.remove();drawRect=null;drawStart=null;
  if((box[2]-box[0])<12||(box[3]-box[1])<12){
    status('Draw a larger rectangle around the product.',true);return;
  }
  if(editingBoxIndex>=0){const target=items[editingBoxIndex];if(target){target.box=box;if(target.manual){target.box=box}else if(data?.items?.[target._id])data.items[target._id].box=[...box];}editingBoxIndex=-1;manualMode=false;wrap.classList.remove('drawing');$('manual').textContent='+ Draw item';render();status('Product box updated.');return;}
  const name=prompt('Product name (for example: Sneakers / Shoes):','Fashion item');
  if(name===null){status('Manual detection cancelled.');return;}
  manualItems.push({label:name.trim()||'Fashion item',score:1,manual:true,box});
  manualMode=false;wrap.classList.remove('drawing');
  $('manual').textContent='+ Draw item';
  items.push(manualItems[manualItems.length-1]);
  active=items.length-1;render();status('Manual product added. You can edit its name in the card.');
});
$('exportCSV').onclick=()=>{const rows=[['Image ID','Product','Store','Price INR','URL','Saved At'],...savedLinks.map(x=>[imageId,x.name,new URL(x.url).hostname,x.price??'',x.url,x.savedAt])];const csv=rows.map(row=>row.map(x=>'"'+String(x).replace(/"/g,'""')+'"').join(',')).join('\r\n');const u=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=u;a.download='fashion-shopping-links.csv';a.click();setTimeout(()=>URL.revokeObjectURL(u),1500)};
$('clearLinks').onclick=()=>{if(confirm('Clear saved shopping links for THIS image?')){savedLinks.splice(0);persistLinks();render()}};
$('export').onclick=()=>{if(!data)return;const payload={image:{id:imageId,width:data.width,height:data.height},shopping_links:savedLinks,items:items.map(x=>({name:x.label,confidence:x.score,x:Math.round((x.box[0]+x.box[2])*50/data.width*10)/10,y:Math.round((x.box[1]+x.box[3])*50/data.height*10)/10,box:{xmin:x.box[0],ymin:x.box[1],xmax:x.box[2],ymax:x.box[3]}}))};const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='fashion-hotspots.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1500)};

function showDiagnostics(){const panel=$('diagnostics');if(!panel)return;panel.replaceChildren();if(!data?.diagnostics){panel.textContent='Run detection to see model predictions.';return;}const d=data.diagnostics;const summary=document.createElement('p');summary.textContent=`Raw: ${d.raw} · Product candidates: ${d.product_candidates} · Components: ${d.component} · Unmapped: ${d.unmapped} · Other: ${d.not_clothing} · After deduplication: ${d.after_deduplication} · Verification rejected: ${d.verification_rejected||0}`;panel.append(summary);const list=document.createElement('div');list.className='raw-list';for(const item of (data.raw_detections||[]).slice(0,50)){const row=document.createElement('div');row.textContent=`${Math.round(item.score*100)}% · ${item.label} → ${item.category||item.reason}`;list.append(row)}panel.append(list)}

// V50: automatically load a saved Instagram/Pinterest image from the collector.
(async()=>{const id=new URLSearchParams(location.search).get('import');collectorOutfitKey=new URLSearchParams(location.search).get('outfit')||'';if(!id||!/^[a-f0-9]{32}$/.test(id))return;try{const response=await fetch('/import-image/'+id,{cache:'no-store'});if(!response.ok)throw Error('Image expired or unavailable');const blob=await response.blob();const extension=blob.type==='image/png'?'png':blob.type==='image/webp'?'webp':'jpg';const f=new File([blob],'collected-fashion.'+extension,{type:blob.type});const dt=new DataTransfer();dt.items.add(f);const input=document.getElementById('upload');input.files=dt.files;window.fmcImportLoading=true;input.dispatchEvent(new Event('change',{bubbles:true}));window.fmcImportLoading=false;history.replaceState(null,'','/');status('Collected image loaded. Starting AI detection…');setTimeout(()=>{if(file&&!loading)$('detect').click()},350)}catch(e){status('Unable to load collected image: '+e.message,true)}})();
