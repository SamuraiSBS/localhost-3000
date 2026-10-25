// A static optical sampler over our own artwork. No DOM screenshots or RAF loop.
const VERTEX = `attribute vec2 position; varying vec2 uv;
void main(){ uv=position*.5+.5; gl_Position=vec4(position,0.,1.); }`;
const FRAGMENT = `precision mediump float;
varying vec2 uv; uniform sampler2D image; uniform vec2 resolution;
uniform vec4 lens; uniform vec4 inkArea; uniform float radius; uniform float strength;
uniform float dispersion; uniform float enabled; uniform float imageAspect;
vec3 scene(vec2 point) {
  float ratio=resolution.x/resolution.y;
  vec2 scale=ratio>imageAspect?vec2(1.,imageAspect/ratio):vec2(ratio/imageAspect,1.);
  return texture2D(image,(point-.5)*scale+.5).rgb;
}
vec3 softScene(vec2 point) {
  vec2 step=vec2(1.15)/resolution;
  return scene(point)*.4+(scene(point+vec2(step.x,0.))+scene(point-vec2(step.x,0.))+scene(point+vec2(0.,step.y))+scene(point-vec2(0.,step.y)))*.15;
}
float roundedBox(vec2 point,vec2 halfSize,float corner) {
  vec2 d=abs(point)-halfSize+corner;
  return length(max(d,0.))+min(max(d.x,d.y),0.)-corner;
}
void main(){
  vec2 pixel=uv*resolution;
  vec2 p=pixel-lens.xy;
  vec2 q=abs(p)-lens.zw+radius;
  float distance=length(max(q,0.))+min(max(q.x,q.y),0.)-radius;
  float inside=1.-smoothstep(-1.,1.,distance);
  vec2 outward=max(q,0.);
  vec2 normal=(length(outward)>.001?normalize(outward):(q.x>q.y?vec2(1.,0.):vec2(0.,1.)))*sign(p);
  float rim=pow(1.-smoothstep(0.,24.,-distance),1.3);
  vec2 shift=(normal*strength*rim*(1.-.35*rim)-p*.012)*inside*enabled/resolution;
  vec3 color=scene(uv);
  vec2 fringe=normal*dispersion*rim/resolution;
  vec3 refracted=vec3(scene(uv+shift+fringe).r,scene(uv+shift).g,scene(uv+shift-fringe).b);
  refracted=mix(refracted,softScene(uv+shift),.55);
  color=mix(color,refracted,inside*enabled);
  color=mix(color,vec3(.7),inside*.045);
  float reflection=pow(max(dot(normal,normalize(vec2(-.6,.8))),0.),5.);
  color*=1.-inside*rim*.12*(1.-reflection);
  float inkDistance=roundedBox(pixel-inkArea.xy,inkArea.zw,18.);
  float scrim=(1.-smoothstep(-2.,28.,inkDistance))*inside;
  color=mix(color,vec3(.018),scrim*.68);
  float edge=exp(-abs(distance+.7)*1.8)*enabled;
  color+=vec3(edge*(.06+.55*reflection)+inside*pow(rim,10.)*.018);
  gl_FragColor=vec4(color,1.);
}`;

export class PrismMaterial {
  constructor(stage) {
    this.stage = stage;
    this.canvas = stage.querySelector("canvas");
    this.image = stage.querySelector("img");
    this.lens = stage.querySelector(".prism-lens");
    this.toggle = document.querySelector("#refraction-toggle");
    this.notice = document.querySelector("#material-fallback");
    this.transparency = matchMedia("(prefers-reduced-transparency: reduce)");
    this.colors = matchMedia("(forced-colors: active)");
    this.toggle.addEventListener("change", () => this.render());
    for (const query of [this.transparency, this.colors]) query.addEventListener("change", () => this.render());
    this.image.addEventListener("load", () => this.render());
    this.canvas.addEventListener("webglcontextlost", event => {
      event.preventDefault(); this.gl = null; this.fallback();
    });
    this.canvas.addEventListener("webglcontextrestored", () => { this.gl = null; this.render(); });
    this.observer = new ResizeObserver(() => this.render());
    this.observer.observe(stage);
    this.observer.observe(this.lens);
  }

  fallback() {
    this.stage.dataset.renderer = "fallback";
    this.notice.hidden = false;
  }

  initialize() {
    const gl = this.canvas.getContext("webgl", { alpha: false, antialias: false, depth: false, powerPreference: "low-power" });
    if (!gl || gl.isContextLost()) return false;
    const compile = (type, source) => {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source); gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error("Shader compilation failed");
      return shader;
    };
    const program = gl.createProgram();
    const vertex = compile(gl.VERTEX_SHADER, VERTEX), fragment = compile(gl.FRAGMENT_SHADER, FRAGMENT);
    gl.attachShader(program, vertex); gl.attachShader(program, fragment); gl.linkProgram(program);
    gl.deleteShader(vertex); gl.deleteShader(fragment);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error("Shader link failed");
    gl.useProgram(program);
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(program, "position");
    gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    const texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, this.image);
    this.gl = gl; this.program = program;
    this.uniforms = Object.fromEntries(["resolution","lens","inkArea","radius","strength","dispersion","enabled","imageAspect"].map(name => [name, gl.getUniformLocation(program, name)]));
    return true;
  }

  render() {
    if (!this.image.complete || !this.image.naturalWidth || !this.stage.getClientRects().length) return;
    if (this.transparency.matches || this.colors.matches || !this.toggle.checked) { this.fallback(); return; }
    const bounds = this.stage.getBoundingClientRect();
    if (!bounds.width || !bounds.height || document.hidden) return;
    try {
      if (!this.gl && !this.initialize()) { this.fallback(); return; }
      const gl = this.gl, u = this.uniforms;
      const dpr = Math.min(devicePixelRatio || 1, 1.5);
      const width = Math.round(bounds.width*dpr), height = Math.round(bounds.height*dpr);
      if (this.canvas.width !== width || this.canvas.height !== height) { this.canvas.width = width; this.canvas.height = height; }
      gl.viewport(0, 0, width, height);
      const panel = this.lens.getBoundingClientRect();
      const ink = this.lens.querySelector(".prism-lens__copy").getBoundingClientRect();
      const tokens = getComputedStyle(document.documentElement);
      gl.uniform2f(u.resolution, bounds.width, bounds.height);
      gl.uniform4f(u.lens, panel.left-bounds.left+panel.width/2, bounds.bottom-panel.top-panel.height/2, panel.width/2, panel.height/2);
      gl.uniform4f(u.inkArea, ink.left-bounds.left+ink.width/2, bounds.bottom-ink.top-ink.height/2, ink.width/2, ink.height/2);
      gl.uniform1f(u.radius, parseFloat(getComputedStyle(this.lens).borderRadius) || 12);
      gl.uniform1f(u.strength, parseFloat(tokens.getPropertyValue("--glass-displacement")) || 0);
      gl.uniform1f(u.dispersion, parseFloat(tokens.getPropertyValue("--glass-dispersion")) || 0);
      gl.uniform1f(u.enabled, 1);
      gl.uniform1f(u.imageAspect, this.image.naturalWidth/this.image.naturalHeight);
      const started = performance.now();
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      if (gl.getError() !== gl.NO_ERROR) throw new Error("Drawing failed");
      this.stage.dataset.renderer = "webgl";
      this.stage.dataset.drawMs = (performance.now()-started).toFixed(2);
      this.notice.hidden = true;
    } catch {
      this.gl = null;
      this.fallback();
    }
  }
}
