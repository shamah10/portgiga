// Minerador da Carteira Portgiga (roda num Worker para não travar a tela).
// Mesmo código do painel: procura o número n que faz sha256(base + n) bater com o alvo.
const K=new Int32Array([0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]);
const W=new Int32Array(64),B=new Uint8Array(64);
function h0(L){ // primeira palavra do sha256 de B[0..L) (mensagem de um bloco so); as outras sao conferidas depois
  B.fill(0,L);B[L]=0x80;B[62]=(L*8)>>>8;B[63]=(L*8)&255;
  for(let i=0;i<16;i++)W[i]=(B[4*i]<<24)|(B[4*i+1]<<16)|(B[4*i+2]<<8)|B[4*i+3];
  for(let i=16;i<64;i++){const a=W[i-15],b=W[i-2];
    W[i]=(W[i-16]+(((a>>>7)|(a<<25))^((a>>>18)|(a<<14))^(a>>>3))+W[i-7]+(((b>>>17)|(b<<15))^((b>>>19)|(b<<13))^(b>>>10)))|0}
  let a=0x6a09e667,b=0xbb67ae85|0,c=0x3c6ef372,d=0xa54ff53a|0,e=0x510e527f,f=0x9b05688c|0,g=0x1f83d9ab,h=0x5be0cd19;
  for(let i=0;i<64;i++){
    const t1=(h+(((e>>>6)|(e<<26))^((e>>>11)|(e<<21))^((e>>>25)|(e<<7)))+((e&f)^(~e&g))+K[i]+W[i])|0;
    const t2=((((a>>>2)|(a<<30))^((a>>>13)|(a<<19))^((a>>>22)|(a<<10)))+((a&b)^(a&c)^(b&c)))|0;
    h=g;g=f;f=e;e=(d+t1)|0;d=c;c=b;b=a;a=(t1+t2)|0}
  return [(0x6a09e667+a)|0,(0xbb67ae85+b)|0,(0x3c6ef372+c)|0,(0xa54ff53a+d)|0,(0x510e527f+e)|0,(0x9b05688c+f)|0,(0x1f83d9ab+g)|0,(0x5be0cd19+h)|0];
}
onmessage=ev=>{
  const {base,alvo,max}=ev.data,alv=[];for(let i=0;i<8;i++)alv.push(parseInt(alvo.substr(i*8,8),16)|0);
  const t0=Date.now();let ultimo=t0;
  for(let n=0;n<max;n++){
    for(let i=0;i<base.length;i++)B[i]=base.charCodeAt(i);
    const s=''+n;for(let i=0;i<s.length;i++)B[base.length+i]=s.charCodeAt(i);
    const r=h0(base.length+s.length);
    if(r[0]===alv[0]&&r[1]===alv[1]&&r[7]===alv[7]){postMessage({fim:true,nonce:n,hps:(n+1)/Math.max(.001,(Date.now()-t0)/1000)});return}
    if((n&16383)===0){const ag=Date.now();if(ag-ultimo>250){ultimo=ag;postMessage({hps:n/Math.max(.001,(ag-t0)/1000),n:n,hash:(r[0]>>>0).toString(16).padStart(8,'0')+(r[1]>>>0).toString(16).padStart(8,'0')+(r[2]>>>0).toString(16).padStart(8,'0')+'…'})}}
  }
  postMessage({fim:true,nonce:-1});
};
