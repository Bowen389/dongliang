"""Independent public TDX daily package downloader and causal OHLC adjustment."""
import csv,io,pathlib,struct,time,zipfile
from urllib.request import Request, urlopen
ROOT=pathlib.Path(__file__).resolve().parent/'data'/'prices'
def package(day):
 ROOT.mkdir(parents=True,exist_ok=True);dest=ROOT/(day+'.csv');ymd=day.replace('-','')
 if dest.exists():return day,'cached'
 url='https://www.tdx.com.cn/products/data/data/g4day/'+ymd+'.zip';error=None
 for attempt in range(3):
  try:
   with urlopen(Request(url, headers={'User-Agent':'Mozilla/5.0'}), timeout=45) as response:
    data=response.read()
   break
  except Exception as e:error=e;time.sleep(1+attempt)
 else:raise RuntimeError('TDX data download failed: '+str(error))
 z=zipfile.ZipFile(io.BytesIO(data));rows=[];market_counts={}
 for market in ['sh','sz']:
  cod=z.read(market+ymd[2:]+'.cod');md=z.read(market+ymd[2:]+'.md1');n=0
  if len(cod)%150 or len(md)%512 or len(cod)//150!=len(md)//512:raise ValueError('invalid package lengths')
  for off in range(0,len(cod),150):
   rec=cod[off:off+150];code=rec[:6].rstrip(b'\0 ').decode('ascii');seq=struct.unpack('<H',rec[32:34])[0];b=md[seq*512:(seq+1)*512]
   if len(b)!=512:raise ValueError('invalid quote index')
   prev=struct.unpack('<d',b[4:12])[0];o,h,l,c=struct.unpack('<4d',b[12:44])
   if c<=0:continue
   n+=1
   if not ((market=='sh' and code.startswith('6')) or (market=='sz' and code.startswith('0'))):continue
   name=rec[40:72].split(b'\0')[0].decode('gbk').strip();vol=struct.unpack('<Q',b[56:64])[0];amount=struct.unpack('<d',b[72:80])[0]
   rows.append([day,market,code,name,prev,o,h,l,c,vol,amount])
  market_counts[market]=n
 if market_counts['sh']<=10000 or market_counts['sz']<=3000:raise ValueError('incomplete TDX package '+str(market_counts))
 tmp=dest.with_suffix('.tmp')
 with tmp.open('w',newline='',encoding='utf8') as f:
  w=csv.writer(f);w.writerow(['date','market','code','name','prev_close','open','high','low','close','volume','amount']);w.writerows(rows)
 tmp.replace(dest);return day,len(rows)
