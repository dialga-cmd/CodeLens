(globalThis.TURBOPACK||(globalThis.TURBOPACK=[])).push(["object"==typeof document?document.currentScript:void 0,18566,(e,i,t)=>{i.exports=e.r(76562)},75254,e=>{"use strict";var i=e.i(71645);let t=(...e)=>e.filter((e,i,t)=>!!e&&t.indexOf(e)===i).join(" ");var r={xmlns:"http://www.w3.org/2000/svg",width:24,height:24,viewBox:"0 0 24 24",fill:"none",stroke:"currentColor",strokeWidth:2,strokeLinecap:"round",strokeLinejoin:"round"};let n=(0,i.forwardRef)(({color:e="currentColor",size:n=24,strokeWidth:o=2,absoluteStrokeWidth:s,className:a="",children:l,iconNode:d,...c},m)=>(0,i.createElement)("svg",{ref:m,...r,width:n,height:n,stroke:e,strokeWidth:s?24*Number(o)/Number(n):o,className:t("lucide",a),...c},[...d.map(([e,t])=>(0,i.createElement)(e,t)),...Array.isArray(l)?l:[l]]));e.s(["default",0,(e,r)=>{let o=(0,i.forwardRef)(({className:o,...s},a)=>(0,i.createElement)(n,{ref:a,iconNode:r,className:t(`lucide-${e.replace(/([a-z0-9])([A-Z])/g,"$1-$2").toLowerCase()}`,o),...s}));return o.displayName=`${e}`,o}],75254)},94542,e=>{"use strict";var i=e.i(47167),t=e.i(18050),r=e.i(71645),n=e.i(18566),o=e.i(54338),s=e.i(32009);let a=()=>{let e=(0,r.useRef)(null);return(0,r.useEffect)(()=>{let i,t=e.current,r=new s.Scene,n=new s.OrthographicCamera(-1,1,1,-1,0,1),o=new s.WebGLRenderer({antialias:!0});o.setSize(window.innerWidth,window.innerHeight),t.appendChild(o.domElement);let a=new s.ShaderMaterial({uniforms:{iTime:{value:0},iResolution:{value:new s.Vector2(window.innerWidth,window.innerHeight)}},vertexShader:`
        void main() {
          gl_Position = vec4(position, 1.0);
        }
      `,fragmentShader:`
        uniform float iTime;
        uniform vec2 iResolution;

        #define NUM_OCTAVES 3

        float rand(vec2 n) {
          return fract(sin(dot(n, vec2(12.9898, 4.1414))) * 43758.5453);
        }

        float noise(vec2 p) {
          vec2 ip = floor(p);
          vec2 u = fract(p);
          u = u*u*(3.0-2.0*u);

          float res = mix(
            mix(rand(ip), rand(ip + vec2(1.0, 0.0)), u.x),
            mix(rand(ip + vec2(0.0, 1.0)), rand(ip + vec2(1.0, 1.0)), u.x), u.y);
          return res * res;
        }

        float fbm(vec2 x) {
          float v = 0.0;
          float a = 0.3;
          vec2 shift = vec2(100);
          mat2 rot = mat2(cos(0.5), sin(0.5), -sin(0.5), cos(0.5));
          for (int i = 0; i < NUM_OCTAVES; ++i) {
            v += a * noise(x);
            x = rot * x * 2.0 + shift;
            a *= 0.4;
          }
          return v;
        }

        void main() {
          vec2 shake = vec2(sin(iTime * 1.2) * 0.005, cos(iTime * 2.1) * 0.005);
          vec2 p = ((gl_FragCoord.xy + shake * iResolution.xy) - iResolution.xy * 0.5) / iResolution.y * mat2(6.0, -4.0, 4.0, 6.0);
          vec2 v;
          vec4 o = vec4(0.0);

          float f = 2.0 + fbm(p + vec2(iTime * 5.0, 0.0)) * 0.5;

          for (float i = 0.0; i < 35.0; i++) {
            v = p + cos(i * i + (iTime + p.x * 0.08) * 0.025 + i * vec2(13.0, 11.0)) * 3.5 + vec2(sin(iTime * 3.0 + i) * 0.003, cos(iTime * 3.5 - i) * 0.003);
            float tailNoise = fbm(v + vec2(iTime * 0.5, i)) * 0.3 * (1.0 - (i / 35.0));
            vec4 auroraColors = vec4(
              0.1 + 0.3 * sin(i * 0.2 + iTime * 0.4),
              0.3 + 0.5 * cos(i * 0.3 + iTime * 0.5),
              0.7 + 0.3 * sin(i * 0.4 + iTime * 0.3),
              1.0
            );
            vec4 currentContribution = auroraColors * exp(sin(i * i + iTime * 0.8)) / length(max(v, vec2(v.x * f * 0.015, v.y * 1.5)));
            float thinnessFactor = smoothstep(0.0, 1.0, i / 35.0) * 0.6;
            o += currentContribution * (1.0 + tailNoise * 0.8) * thinnessFactor;
          }

          o = tanh(pow(o / 100.0, vec4(1.6)));
          gl_FragColor = o * 1.5;
        }
      `}),l=new s.PlaneGeometry(2,2),d=new s.Mesh(l,a);r.add(d);let c=()=>{a.uniforms.iTime.value+=.016,o.render(r,n),i=requestAnimationFrame(c)};c();let m=()=>{o.setSize(window.innerWidth,window.innerHeight),a.uniforms.iResolution.value.set(window.innerWidth,window.innerHeight)};return window.addEventListener("resize",m),()=>{cancelAnimationFrame(i),window.removeEventListener("resize",m),t.removeChild(o.domElement),l.dispose(),a.dispose(),o.dispose()}},[]),(0,t.jsx)("div",{ref:e,className:"relative overflow-x-hidden",children:(0,t.jsx)("div",{className:"relative z-10 divider"})})};var l=e.i(31278),d=e.i(75254);let c=(0,d.default)("BookOpen",[["path",{d:"M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z",key:"vv98re"}],["path",{d:"M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z",key:"1cyq3y"}]]),m=(0,d.default)("Power",[["path",{d:"M12 2v10",key:"mnfbl"}],["path",{d:"M18.4 6.6a9 9 0 1 1-12.77.04",key:"obofu9"}]]),f=i.default.env.NEXT_PUBLIC_API_BASE_URL||"http://localhost:8000",u=i.default.env.NEXT_PUBLIC_RENDER_BACKEND_HEALTH_URL||`${f}/health`;e.s(["default",0,function(){let e=(0,n.useRouter)(),{user:i,loading:s,loginWithGoogle:d}=(0,o.useAuth)();return((0,r.useEffect)(()=>{!s&&i&&e.push("/dashboard")},[i,s,e]),s)?(0,t.jsx)("div",{className:"min-h-screen flex items-center justify-center bg-black",children:(0,t.jsx)(l.Loader2,{className:"animate-spin text-[#00ff41]",size:32})}):(0,t.jsxs)("div",{className:"relative min-h-screen w-full overflow-hidden bg-black",children:[(0,t.jsx)(a,{}),(0,t.jsx)("div",{className:"absolute top-4 right-4 z-20",children:(0,t.jsxs)("a",{href:u,target:"_blank",rel:"noreferrer",className:"inline-flex items-center gap-2 px-3 py-1.5 rounded-md border border-[#00ff41]/70 bg-black/40 text-[#00ff41] text-[11px] font-semibold uppercase tracking-[0.18em] hover:bg-[#00ff41]/10 hover:border-[#00ff41] transition-all shadow-[0_0_18px_rgba(0,255,65,0.12)]",children:[(0,t.jsx)(m,{size:12})," turn backend on"]})}),(0,t.jsx)("div",{className:"absolute inset-0 flex items-center justify-center z-10",children:(0,t.jsxs)("div",{className:"text-center space-y-8 px-4",children:[(0,t.jsxs)("div",{className:"space-y-4",children:[(0,t.jsx)("h1",{className:"text-5xl md:text-6xl font-bold text-white tracking-tighter",children:"CodeLens"}),(0,t.jsx)("p",{className:"text-xl text-[#888] max-w-md mx-auto",children:"AI-Powered Codebase Analysis & Visualization"})]}),(0,t.jsxs)("div",{className:"flex flex-col items-center gap-3",children:[(0,t.jsx)("button",{onClick:d,className:"px-8 py-3 bg-[#00ff41] text-black rounded-lg font-bold text-lg hover:bg-[#00ff41]/90 transition-all shadow-lg hover:shadow-xl",children:"Login with Google"}),(0,t.jsxs)("button",{onClick:()=>e.push("/documentation"),className:"px-6 py-2.5 bg-transparent text-[#00ff41] border border-[#00ff41]/70 rounded-md font-semibold text-sm hover:bg-[#00ff41]/10 hover:border-[#00ff41] transition-all inline-flex items-center gap-2",children:[(0,t.jsx)(c,{size:16})," Documentation"]})]}),(0,t.jsx)("p",{className:"text-[#666] text-sm",children:"Analyze any public GitHub repository with AI-powered insights"})]})})]})}],94542)}]);