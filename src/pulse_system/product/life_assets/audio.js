// A bounded mono 16kHz ring producer. No microphone is opened by this module.
class LifePCM extends AudioWorkletProcessor {
  constructor(){super();this.acc=0;this.n=0;this.phase=0;this.out=[];this.start=0;}
  process(inputs){const input=inputs[0];if(!input?.[0])return true;const data=input[0];for(let i=0;i<data.length;i++){
    let value=0;for(const channel of input)value+=channel[i]/input.length;
    this.acc+=value;this.n++;this.phase+=16000;
    if(this.phase>=sampleRate){this.phase-=sampleRate;if(!this.out.length)this.start=currentTime+i/sampleRate;
      this.out.push(Math.max(-32768,Math.min(32767,Math.round(this.acc/this.n*32767))));this.acc=0;this.n=0;
      if(this.out.length===8000){const pcm=new Int16Array(this.out);this.port.postMessage({t:this.start,pcm},[pcm.buffer]);this.out=[];}
    }}return true;}
}
registerProcessor('life-pcm',LifePCM);
