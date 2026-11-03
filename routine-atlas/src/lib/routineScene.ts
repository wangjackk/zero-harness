import * as THREE from 'three'
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js'
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js'

interface Body {
  group: THREE.Group
  surface: THREE.Group
  orbit: THREE.Line
  moons: Moon[]
  radius: number
  size: number
  speed: number
  phase: number
  inclination: number
  born: number
}

interface Moon {
  group: THREE.Group
  orbit: THREE.Line
  radius: number
  speed: number
  phase: number
  born: number
  duration: number
  interval: number
}

interface Minor {
  mesh: THREE.Mesh
  glow: THREE.Sprite
  radius: number
  speed: number
  phase: number
  born: number
  duration: number
  interval: number
  inclination: number
}

interface Comet {
  curve: THREE.CubicBezierCurve3
  line: THREE.Line
  head: THREE.Sprite
  duration: number
  offset: number
}

interface CodeGlyph {
  sprite: THREE.Sprite
  origin: THREE.Vector3
  orbit: THREE.Vector3
  end: THREE.Vector3
  delay: number
}

interface CallBirth {
  line: THREE.Line
  head: THREE.Sprite
  label: THREE.Sprite
  glyphs: CodeGlyph[]
  curve: THREE.CubicBezierCurve3
}

const CYAN = new THREE.Color('#79e8ff')
const VIOLET = new THREE.Color('#ad91ff')
const DURATION = 44

function clamp01(value: number) { return Math.max(0, Math.min(1, value)) }
function smooth(value: number) { const t = clamp01(value); return t * t * (3 - 2 * t) }
function smoother(value: number) { const t = clamp01(value); return t * t * t * (t * (t * 6 - 15) + 10) }
function lifeScale(age: number, duration: number) {
  return smooth(age / 1.1) * (1 - smooth((age - duration + 1.5) / 1.5))
}
function randomGenerator(seed: number) {
  return () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647 }
}

function radialTexture(stops: Array<[number, string]>) {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 256
  const ctx = canvas.getContext('2d')!
  const gradient = ctx.createRadialGradient(128, 128, 0, 128, 128, 128)
  stops.forEach(([at, color]) => gradient.addColorStop(at, color))
  ctx.fillStyle = gradient
  ctx.fillRect(0, 0, 256, 256)
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  return texture
}

function codeTexture(text: string, color = '#b9e9f5', fontSize = 54) {
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d')!
  ctx.font = `400 ${fontSize}px "JetBrains Mono", monospace`
  canvas.width = Math.max(128, Math.ceil(ctx.measureText(text).width + 40))
  canvas.height = 128
  ctx.font = `400 ${fontSize}px "JetBrains Mono", monospace`
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.shadowColor = color
  ctx.shadowBlur = 7
  ctx.lineWidth = 4
  ctx.strokeStyle = 'rgba(2, 7, 15, .88)'
  ctx.strokeText(text, canvas.width / 2, 64)
  ctx.fillStyle = color
  ctx.fillText(text, canvas.width / 2, 64)
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.minFilter = THREE.LinearFilter
  return texture
}

function planetTexture(base: string, light: string, seed: number) {
  const canvas = document.createElement('canvas')
  canvas.width = 512
  canvas.height = 256
  const ctx = canvas.getContext('2d')!
  const random = randomGenerator(seed)
  const gradient = ctx.createLinearGradient(0, 0, 0, 256)
  gradient.addColorStop(0, light)
  gradient.addColorStop(.45, base)
  gradient.addColorStop(1, '#07121d')
  ctx.fillStyle = gradient
  ctx.fillRect(0, 0, 512, 256)
  for (let i = 0; i < 150; i++) {
    const y = random() * 256
    ctx.strokeStyle = `rgba(220,245,255,${.015 + random() * .075})`
    ctx.lineWidth = .5 + random() * 5
    ctx.beginPath()
    ctx.moveTo(-20, y)
    ctx.bezierCurveTo(120, y + random() * 22 - 11, 370, y + random() * 22 - 11, 532, y)
    ctx.stroke()
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.wrapS = THREE.RepeatWrapping
  return texture
}

function makeLine(points: THREE.Vector3[], color: THREE.ColorRepresentation, opacity: number) {
  return new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(points),
    new THREE.LineBasicMaterial({ color, transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false })
  )
}

function orbitPoints(radius: number, inclination = 0) {
  const points: THREE.Vector3[] = []
  for (let i = 0; i <= 160; i++) {
    const angle = i / 160 * Math.PI * 2
    points.push(new THREE.Vector3(Math.cos(angle) * radius, Math.sin(angle) * radius * inclination, Math.sin(angle) * radius))
  }
  return points
}

/** 奇点 → 爆散 → 聚合 → 恒星系 → 星系群 → 坍缩。视觉尺度不表示类型层级。 */
export class RoutineScene {
  private renderer: THREE.WebGLRenderer
  private composer: EffectComposer
  private scene = new THREE.Scene()
  private camera = new THREE.PerspectiveCamera(42, 1, .1, 400)
  private universe = new THREE.Group()
  private system = new THREE.Group()
  private primary = new THREE.Group()
  private companion = new THREE.Group()
  private clusters = new THREE.Group()
  private stars: THREE.Points
  private dust: THREE.Points
  private galaxy: THREE.Points
  private bodies: Body[] = []
  private minors: Minor[] = []
  private comets: Comet[] = []
  private callBirth!: CallBirth
  private starMaterial: THREE.ShaderMaterial
  private starLight: THREE.PointLight
  private bloom: UnrealBloomPass
  private glowTexture: THREE.CanvasTexture
  private explosionClouds: THREE.Sprite[] = []
  private home = new THREE.Vector3(8, -.3, 0)
  private active = false
  private frame = 0
  private last = 0
  private time = 0
  private phase = ''
  private pointer = new THREE.Vector2()
  private pointerTarget = new THREE.Vector2()
  private baseCameraZ = 32
  private lightTheme = false
  private dustDirections: Float32Array
  private dustData: Float32Array
  private dustTargets: Uint8Array
  private target = new THREE.Vector3()

  constructor(private canvas: HTMLCanvasElement, private onPhase: (phase: string) => void = () => {}) {
    this.renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'high-performance' })
    this.renderer.setClearColor(0x000000, 0)
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75))
    this.renderer.outputColorSpace = THREE.SRGBColorSpace
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping
    this.renderer.toneMappingExposure = .72
    this.scene.fog = new THREE.FogExp2(0x06070a, .004)
    this.camera.position.set(0, 7.5, this.baseCameraZ)

    this.composer = new EffectComposer(this.renderer)
    this.composer.addPass(new RenderPass(this.scene, this.camera))
    this.bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), .48, .5, .78)
    this.composer.addPass(this.bloom)
    this.composer.addPass(new OutputPass())
    this.glowTexture = radialTexture([
      [0, 'rgba(255,255,255,1)'], [.05, 'rgba(225,252,255,.98)'],
      [.18, 'rgba(100,220,255,.4)'], [.5, 'rgba(65,135,255,.075)'], [1, 'rgba(0,0,0,0)']
    ])

    this.stars = this.createStarfield()
    this.scene.add(this.stars, this.universe)
    this.addNebulae()
    this.scene.add(new THREE.AmbientLight(0x7899bb, .65))
    const fill = new THREE.DirectionalLight(0xa9c8ee, 1.35)
    fill.position.set(-8, 12, 16)
    this.scene.add(fill)

    this.universe.add(this.system, this.clusters)
    this.system.add(this.primary, this.companion)
    this.starMaterial = this.createPrimaryStar()
    this.starLight = new THREE.PointLight(0x9eeaff, 0, 80, 1.65)
    this.primary.add(this.starLight)
    this.galaxy = this.createSpiralGalaxy(9200, 20, 193)
    this.system.add(this.galaxy)
    this.createOrbits()
    this.createCallBirth()
    this.createMinorInstances()
    this.createComets()
    this.createCompanion()
    this.createClusters()
    this.createExplosionClouds()
    const accretion = this.createAccretionDust(6200)
    this.dust = accretion.points
    this.dustDirections = accretion.directions
    this.dustData = accretion.data
    this.dustTargets = accretion.targets
    this.system.add(this.dust)
    this.update(0)
  }

  private sprite(scale: number, opacity = 1, texture = this.glowTexture) {
    const material = new THREE.SpriteMaterial({ map: texture, transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false })
    const sprite = new THREE.Sprite(material)
    sprite.scale.set(scale, scale, 1)
    return sprite
  }

  private createStarfield() {
    const count = 5200
    const positions = new Float32Array(count * 3)
    const colors = new Float32Array(count * 3)
    const random = randomGenerator(71)
    for (let i = 0; i < count; i++) {
      positions[i * 3] = (random() - .5) * 240
      positions[i * 3 + 1] = (random() - .5) * 150
      positions[i * 3 + 2] = -10 - random() * 230
      new THREE.Color().lerpColors(CYAN, VIOLET, random()).multiplyScalar(.3 + random() * .85).toArray(colors, i * 3)
    }
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3))
    return new THREE.Points(geometry, new THREE.PointsMaterial({ size: .105, vertexColors: true, transparent: true, opacity: .8, blending: THREE.AdditiveBlending, depthWrite: false }))
  }

  private addNebulae() {
    const cyan = radialTexture([[0, 'rgba(30,125,190,.2)'], [.35, 'rgba(25,75,135,.11)'], [1, 'rgba(0,0,0,0)']])
    const violet = radialTexture([[0, 'rgba(104,65,180,.17)'], [.4, 'rgba(65,40,120,.08)'], [1, 'rgba(0,0,0,0)']])
    const a = this.sprite(92, .18, cyan)
    a.position.set(5, -2, -54)
    const b = this.sprite(78, .2, violet)
    b.position.set(24, 9, -62)
    this.scene.add(a, b)
  }

  private createPrimaryStar() {
    const material = new THREE.ShaderMaterial({
      uniforms: { uTime: { value: 0 } },
      vertexShader: 'varying vec3 vP; varying vec3 vN; void main(){vP=position;vN=normalize(normalMatrix*normal);gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',
      fragmentShader: `
        uniform float uTime; varying vec3 vP; varying vec3 vN;
        void main(){
          vec3 p=normalize(vP);
          float a=sin(p.x*13.+sin(p.z*8.+uTime*.23)*2.+uTime*.32);
          float b=sin(p.y*19.-uTime*.27+sin(p.x*9.)*2.);
          float c=sin((p.x+p.y+p.z)*29.+uTime*.18);
          float cells=smoothstep(-.58,.76,(a+b+c)/3.);
          vec3 col=mix(vec3(.018,.09,.22),vec3(.36,.82,1.12),cells);
          col+=vec3(.08,.38,.72)*pow(1.-abs(vN.z),2.4);
          gl_FragColor=vec4(col,1.);
        }`
    })
    this.primary.add(new THREE.Mesh(new THREE.SphereGeometry(2.65, 96, 64), material))
    const atmosphere = new THREE.Mesh(
      new THREE.SphereGeometry(2.92, 64, 48),
      new THREE.ShaderMaterial({
        vertexShader: 'varying vec3 vN; void main(){vN=normalize(normalMatrix*normal);gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',
        fragmentShader: 'varying vec3 vN; void main(){float a=pow(1.-abs(vN.z),2.2);gl_FragColor=vec4(.35,.8,1.,a*.32);}',
        transparent: true, blending: THREE.AdditiveBlending, side: THREE.BackSide, depthWrite: false
      })
    )
    this.primary.add(atmosphere, this.sprite(13, .24), this.sprite(6.5, .32))
    const flare = this.sprite(1, .48)
    flare.scale.set(18, .04, 1)
    this.primary.add(flare)
    return material
  }

  private createSpiralGalaxy(count: number, radius: number, seed: number) {
    const positions = new Float32Array(count * 3)
    const colors = new Float32Array(count * 3)
    const random = randomGenerator(seed)
    for (let i = 0; i < count; i++) {
      const r = .4 + Math.pow(random(), .68) * radius
      const arm = i % 3
      const angle = arm * Math.PI * 2 / 3 + r * .34 + (random() - .5) * .8
      positions[i * 3] = Math.cos(angle) * r
      positions[i * 3 + 1] = (random() - .5) * (1.7 - Math.min(r / radius, 1)) * (radius / 20)
      positions[i * 3 + 2] = Math.sin(angle) * r
      new THREE.Color().lerpColors(CYAN, VIOLET, Math.min(1, r / radius)).multiplyScalar(.22 + random() * .8).toArray(colors, i * 3)
    }
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3))
    return new THREE.Points(geometry, new THREE.PointsMaterial({ size: .055, vertexColors: true, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }))
  }

  private createAccretionDust(count: number) {
    const positions = new Float32Array(count * 3)
    const colors = new Float32Array(count * 3)
    const directions = new Float32Array(count * 3)
    const data = new Float32Array(count * 4)
    const targets = new Uint8Array(count)
    const random = randomGenerator(977)
    for (let i = 0; i < count; i++) {
      const z = random() * 2 - 1
      const angle = random() * Math.PI * 2
      const radial = Math.sqrt(1 - z * z)
      directions[i * 3] = Math.cos(angle) * radial
      directions[i * 3 + 1] = z
      directions[i * 3 + 2] = Math.sin(angle) * radial
      data[i * 4] = .3 + Math.pow(random(), 1.8) * 3.2
      data[i * 4 + 1] = random() * Math.PI * 2
      data[i * 4 + 2] = (random() - .5) * .9
      data[i * 4 + 3] = random()
      const pick = random()
      targets[i] = pick < .4 ? 0 : pick < .56 ? 1 : pick < .72 ? 2 : pick < .86 ? 3 : 4
      new THREE.Color().lerpColors(CYAN, VIOLET, random()).multiplyScalar(.45 + random() * .85).toArray(colors, i * 3)
    }
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3))
    const points = new THREE.Points(geometry, new THREE.PointsMaterial({ size: .13, vertexColors: true, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }))
    return { points, directions, data, targets }
  }

  private createOrbits() {
    const configs = [
      { radius: 4.7, size: .55, speed: .16, phase: .35, inclination: .1, base: '#247c9e', light: '#b5f3ff', born: 9.2 },
      { radius: 7.5, size: .82, speed: -.085, phase: 2.7, inclination: -.16, base: '#66519e', light: '#d7c8ff', born: 11.2 },
      { radius: 10.7, size: .45, speed: .052, phase: 4.4, inclination: .22, base: '#276e82', light: '#b5f5f2', born: 13.2 }
    ]
    configs.forEach((config, index) => {
      const orbit = makeLine(orbitPoints(config.radius, config.inclination), index === 1 ? 0x9f88d9 : 0x6db7c9, 0)
      this.system.add(orbit)
      const group = new THREE.Group()
      const surface = new THREE.Group()
      const material = new THREE.MeshStandardMaterial({ map: planetTexture(config.base, config.light, 43 + index * 79), roughness: .58, metalness: .12, emissive: new THREE.Color(config.base), emissiveIntensity: .09, transparent: true })
      surface.add(new THREE.Mesh(new THREE.SphereGeometry(config.size, 48, 32), material), this.sprite(config.size * 5.5, .26))
      if (index === 1) {
        const ring = new THREE.Mesh(new THREE.RingGeometry(config.size * 1.25, config.size * 2.05, 96), new THREE.MeshBasicMaterial({ color: 0xb6a2e8, transparent: true, opacity: .34, side: THREE.DoubleSide, depthWrite: false }))
        ring.rotation.x = Math.PI / 2.35
        ring.rotation.y = .28
        surface.add(ring)
      }
      group.add(surface)
      group.visible = false
      this.system.add(group)
      const moons: Moon[] = []
      const moonCount = index === 1 ? 2 : 1
      for (let moonIndex = 0; moonIndex < moonCount; moonIndex++) {
        const radius = config.size * (2.3 + moonIndex * .85)
        const moonOrbit = makeLine(orbitPoints(radius, .36), 0x7996b5, 0)
        const moon = new THREE.Group()
        const size = config.size * .19
        moon.add(new THREE.Mesh(new THREE.SphereGeometry(size, 20, 12), new THREE.MeshStandardMaterial({ color: moonIndex ? 0xa99acb : 0x91c7d5, roughness: .8 })), this.sprite(size * 6, .2))
        moon.visible = false
        group.add(moonOrbit, moon)
        moons.push({ group: moon, orbit: moonOrbit, radius, speed: (index % 2 ? -1 : 1) * (.65 + moonIndex * .2), phase: index + moonIndex * 2.4, born: config.born + 2.2 + moonIndex, duration: 8 + index * 2, interval: 13 + index * 2 + moonIndex * 3 })
      }
      this.bodies.push({ group, surface, orbit, moons, ...config })
    })
  }

  /** 一次重点诞生：微小的调用标记沿光迹抵达，字符在轨道上聚合为行星。 */
  private createCallBirth() {
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(64 * 3), 3))
    const line = new THREE.Line(geometry, new THREE.LineBasicMaterial({
      color: 0x8fd7ef, transparent: true, opacity: 0,
      blending: THREE.AdditiveBlending, depthWrite: false
    }))
    const head = this.sprite(.72, 0)
    ;(head.material as THREE.SpriteMaterial).color.set(0x9eeaff)
    const label = this.sprite(1, 0, codeTexture('call()', '#c5edf5', 48))
    label.scale.set(1.55, .5, 1)
    const labelMaterial = label.material as THREE.SpriteMaterial
    labelMaterial.blending = THREE.NormalBlending
    labelMaterial.depthTest = false
    label.renderOrder = 6
    const glyphs: CodeGlyph[] = []
    const random = randomGenerator(1207)
    const source = 'create()start()'
    for (let i = 0; i < source.length; i++) {
      const sprite = this.sprite(1, 0, codeTexture(source[i], i < 8 ? '#a48bd3' : '#b5dfea', 58))
      sprite.scale.set(.28, .28, 1)
      const material = sprite.material as THREE.SpriteMaterial
      material.blending = THREE.NormalBlending
      material.depthTest = false
      sprite.renderOrder = 6
      const row = i < 8 ? 0 : 1
      const column = row ? i - 8 : i
      const count = row ? 7 : 8
      const angle = -1.05 + column / (count - 1) * 2.1
      const radius = row ? 1.85 : 2.05
      const origin = new THREE.Vector3(Math.sin(angle) * radius, row ? -.68 : .68, Math.cos(angle) * radius)
      const orbit = origin.clone().multiplyScalar(.3)
      orbit.y += (random() - .5) * .35
      const end = new THREE.Vector3().randomDirection().multiplyScalar(.2 + random() * .52)
      glyphs.push({ sprite, origin, orbit, end, delay: i * .022 })
      this.system.add(sprite)
    }
    const curve = new THREE.CubicBezierCurve3(
      new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3()
    )
    this.system.add(line, head, label)
    this.callBirth = { line, head, label, glyphs, curve }
  }

  private createMinorInstances() {
    for (let i = 0; i < 18; i++) {
      const radius = 3.8 + (i % 6) * 1.85
      const size = .07 + (i % 3) * .035
      const material = new THREE.MeshBasicMaterial({ color: i % 3 === 0 ? 0xc0aaff : 0xa2efff, transparent: true, opacity: 0 })
      const mesh = new THREE.Mesh(new THREE.SphereGeometry(size, 12, 8), material)
      const glow = this.sprite(size * 9, 0)
      this.system.add(mesh, glow)
      this.minors.push({ mesh, glow, radius, speed: (i % 2 ? -.2 : .24) * (1 + i % 3 * .15), phase: i * 1.73, born: 14 + i * .24, duration: 5.5 + i % 5, interval: 10 + i % 6 * 1.7, inclination: (i % 5 - 2) * .11 })
    }
  }

  private createComets() {
    const curves = [
      new THREE.CubicBezierCurve3(new THREE.Vector3(1.4, .4, 1), new THREE.Vector3(5, 4, -2), new THREE.Vector3(12, 7, -5), new THREE.Vector3(25, 6, -15)),
      new THREE.CubicBezierCurve3(new THREE.Vector3(-1.5, -.2, .6), new THREE.Vector3(-5, -2, 3), new THREE.Vector3(-13, -5, 4), new THREE.Vector3(-25, -7, -6)),
      new THREE.CubicBezierCurve3(new THREE.Vector3(.5, -1.4, -1), new THREE.Vector3(5, -5, -4), new THREE.Vector3(14, -9, -6), new THREE.Vector3(25, -12, -15))
    ]
    curves.forEach((curve, index) => {
      const geometry = new THREE.BufferGeometry()
      geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(40 * 3), 3))
      const line = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: index === 1 ? 0xbcaaff : 0xb9f5ff, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }))
      const head = this.sprite(index === 1 ? 1.1 : 1.45, 0)
      this.system.add(line, head)
      this.comets.push({ curve, line, head, duration: 9 + index * 3.2, offset: index * .31 })
    })
  }

  private createCompanion() {
    this.companion.position.set(12.5, 4.5, -9)
    this.companion.add(new THREE.Mesh(new THREE.SphereGeometry(.82, 48, 32), new THREE.MeshBasicMaterial({ color: new THREE.Color(1.25, 1.05, 2.1) })), this.sprite(6, .35))
    const flare = this.sprite(1, .32)
    flare.scale.set(10, .05, 1)
    this.companion.add(flare)
  }

  private createClusters() {
    const configs = [
      { p: [-30, 15, -25], s: .8, r: .4 },
      { p: [28, -14, -38], s: 1.05, r: -1.1 },
      { p: [-18, -22, -60], s: .65, r: 1.8 },
      { p: [15, 27, -70], s: .72, r: 2.6 }
    ]
    configs.forEach((config, index) => {
      const galaxy = this.createSpiralGalaxy(2400, 10 + index * 1.5, 313 + index * 107)
      galaxy.position.set(config.p[0], config.p[1], config.p[2])
      galaxy.scale.setScalar(config.s)
      galaxy.rotation.x = config.r
      galaxy.rotation.z = config.r * .4
      this.clusters.add(galaxy)
    })
  }

  private createExplosionClouds() {
    const texture = radialTexture([
      [0, 'rgba(170,235,255,.55)'], [.18, 'rgba(90,175,255,.22)'],
      [.52, 'rgba(95,85,210,.08)'], [1, 'rgba(0,0,0,0)']
    ])
    for (let i = 0; i < 4; i++) {
      const cloud = this.sprite(1, 0, texture)
      ;(cloud.material as THREE.SpriteMaterial).color.set(i % 2 ? 0xb399ff : 0x79dfff)
      cloud.position.set((i - 1.5) * 2.2, (i % 2 - .5) * 2.4, -i * 1.3)
      this.system.add(cloud)
      this.explosionClouds.push(cloud)
    }
  }

  private bodyPosition(body: Body, time: number, target = new THREE.Vector3()) {
    const angle = body.phase + time * body.speed
    return target.set(Math.cos(angle) * body.radius, Math.sin(angle) * body.radius * body.inclination, Math.sin(angle) * body.radius)
  }

  private updateAccretion(time: number) {
    const burst = smoother((time - 1.1) / 4.2)
    const gather = smoother((time - 5) / 5.5)
    const positions = this.dust.geometry.getAttribute('position') as THREE.BufferAttribute
    for (let i = 0; i < positions.count; i++) {
      const d = i * 3
      const m = i * 4
      const distance = 32 * burst * (.55 + this.dustData[m + 3] * .65)
      let x = this.dustDirections[d] * distance
      let y = this.dustDirections[d + 1] * distance
      let z = this.dustDirections[d + 2] * distance
      const assignment = this.dustTargets[i]
      if (assignment === 0) this.target.set(0, 0, 0)
      else if (assignment === 4) this.target.copy(this.companion.position)
      else this.bodyPosition(this.bodies[assignment - 1], time, this.target)
      const radius = this.dustData[m]
      const angle = this.dustData[m + 1] + time * (1.1 - gather * .65)
      const localRadius = assignment === 0 ? radius : radius * .34
      const tx = this.target.x + Math.cos(angle) * localRadius
      const ty = this.target.y + this.dustData[m + 2] * (assignment === 0 ? 1 : .35)
      const tz = this.target.z + Math.sin(angle) * localRadius
      const curl = (1 - gather) * (2.4 + this.dustData[m + 3] * 3)
      const gx = x + (tx - x) * gather
      const gy = y + (ty - y) * gather
      const gz = z + (tz - z) * gather
      x = gx * Math.cos(curl) - gz * Math.sin(curl)
      z = gx * Math.sin(curl) + gz * Math.cos(curl)
      y = gy
      positions.setXYZ(i, x, y, z)
    }
    positions.needsUpdate = true
    const material = this.dust.material as THREE.PointsMaterial
    const codeFocus = smooth((time - 7.8) / .5) * (1 - smooth((time - 11.2) / .45))
    material.opacity = smooth((time - 1) / 1.2) * (1 - smooth((time - 12) / 6)) * (this.lightTheme ? .65 : 1) * (1 - codeFocus * .58)
    material.size = .16 + Math.sin(clamp01((time - 5) / 10) * Math.PI) * .13
    this.dust.visible = time > 1 && time < 39
  }

  private updatePrimary(time: number) {
    const formed = smooth((time - 5.2) / 3.3)
    const end = smooth((time - 39) / 3)
    const singularity = .022 + Math.sin(time * 2.4) * .003
    const scale = time < 5.2 ? singularity + formed * .04 : singularity + formed * (1 - end)
    this.primary.scale.setScalar(Math.max(.012, scale))
    this.starLight.intensity = 260 * formed * (1 - end)
    const companionScale = smooth((time - 10.5) / 3) * (1 - end)
    this.companion.scale.setScalar(Math.max(.001, companionScale))
    this.companion.visible = companionScale > .001

    this.explosionClouds.forEach((cloud, index) => {
      const progress = clamp01((time - 1 - index * .16) / 7)
      const scale = 3 + progress * (34 + index * 5)
      cloud.scale.set(scale * (1.2 + index * .08), scale * .72, 1)
      ;(cloud.material as THREE.SpriteMaterial).opacity = Math.sin(progress * Math.PI) * (this.lightTheme ? .12 : .24)
      cloud.visible = progress > 0 && progress < 1
    })
    ;(this.galaxy.material as THREE.PointsMaterial).opacity = (this.lightTheme ? .28 : .62) * smooth((time - 7) / 8) * (1 - smooth((time - 39) / 3))
  }

  private updateBodies(time: number, delta: number) {
    const end = 1 - smooth((time - 38.5) / 3)
    this.bodies.forEach(body => {
      const age = time - body.born
      this.bodyPosition(body, time, this.target)
      body.group.visible = age > 0 && end > 0
      body.group.position.copy(this.target)
      body.surface.scale.setScalar(Math.max(.001, smooth(age / 2) * end))
      ;(body.orbit.material as THREE.LineBasicMaterial).opacity = (body.radius < 5 ? .25 : .16) * smooth(age / 2) * end
      body.surface.rotation.y += delta * .16
      body.moons.forEach(moon => {
        const moonAge = time - moon.born
        const cycleAge = ((moonAge % moon.interval) + moon.interval) % moon.interval
        const alive = moonAge > 0 && cycleAge < moon.duration && end > 0
        const scale = alive ? lifeScale(cycleAge, moon.duration) * end : 0
        moon.group.visible = scale > .005
        moon.group.scale.setScalar(Math.max(.001, scale))
        const angle = moon.phase + time * moon.speed
        moon.group.position.set(Math.cos(angle) * moon.radius, Math.sin(angle) * moon.radius * .36, Math.sin(angle) * moon.radius)
        ;(moon.orbit.material as THREE.LineBasicMaterial).opacity = .13 * smooth(moonAge / 2) * end
      })
    })
  }

  private updateMinorInstances(time: number) {
    const enabled = time > 14 && time < 37
    this.minors.forEach(minor => {
      const absoluteAge = time - minor.born
      const age = ((absoluteAge % minor.interval) + minor.interval) % minor.interval
      const alive = enabled && absoluteAge > 0 && age < minor.duration
      minor.mesh.visible = minor.glow.visible = alive
      if (!alive) return
      const scale = lifeScale(age, minor.duration)
      const angle = minor.phase + time * minor.speed
      minor.mesh.position.set(Math.cos(angle) * minor.radius, Math.sin(angle) * minor.radius * minor.inclination, Math.sin(angle) * minor.radius)
      minor.mesh.scale.setScalar(Math.max(.001, scale))
      ;(minor.mesh.material as THREE.MeshBasicMaterial).opacity = scale
      minor.glow.position.copy(minor.mesh.position)
      minor.glow.scale.setScalar(.8 + (1 - scale) * 1.8)
      ;(minor.glow.material as THREE.SpriteMaterial).opacity = scale * .55
    })
  }

  private updateComets(time: number) {
    this.comets.forEach(comet => {
      const phase = (time / comet.duration + comet.offset) % 1
      const visible = time > 15 && time < 35 && phase < .76
      comet.head.visible = comet.line.visible = visible
      if (!visible) return
      const headAt = phase / .76
      const attribute = comet.line.geometry.getAttribute('position') as THREE.BufferAttribute
      for (let i = 0; i < attribute.count; i++) {
        const t = Math.max(0, headAt - (attribute.count - 1 - i) / attribute.count * .2)
        const p = comet.curve.getPoint(t)
        attribute.setXYZ(i, p.x, p.y, p.z)
      }
      attribute.needsUpdate = true
      comet.head.position.copy(comet.curve.getPoint(headAt))
      const opacity = Math.min(1, headAt * 8, (1 - headAt) * 5)
      ;(comet.head.material as THREE.SpriteMaterial).opacity = opacity
      ;(comet.line.material as THREE.LineBasicMaterial).opacity = opacity * .7
    })
  }

  private updateCallBirth(time: number) {
    const birth = this.callBirth
    const active = time > 8 && time < 11.65
    const focus = smooth((time - 7.8) / .5) * (1 - smooth((time - 11.2) / .45))
    this.bloom.strength = (this.lightTheme ? .32 : .48) * (1 - focus * .38)
    birth.line.visible = birth.head.visible = birth.label.visible = active
    const target = this.bodyPosition(this.bodies[1], time, new THREE.Vector3())
    const travel = smoother((time - 8) / 1.25)

    birth.curve.v0.set(1.8, .45, .65)
    birth.curve.v1.set(3.2, 3.2, 1.8)
    birth.curve.v2.copy(target).add(new THREE.Vector3(-2.2, 2.1, 1.2))
    birth.curve.v3.copy(target)
    const lineAttribute = birth.line.geometry.getAttribute('position') as THREE.BufferAttribute
    for (let i = 0; i < lineAttribute.count; i++) {
      const t = Math.max(0, travel - (lineAttribute.count - 1 - i) / lineAttribute.count * .3)
      const point = birth.curve.getPoint(t)
      lineAttribute.setXYZ(i, point.x, point.y, point.z)
    }
    lineAttribute.needsUpdate = true
    const headPosition = birth.curve.getPoint(travel)
    birth.head.position.copy(headPosition)
    birth.label.position.copy(headPosition).add(new THREE.Vector3(0, .46, 0))
    const callOpacity = active ? Math.max(0, Math.min(1, (time - 8) * 5, (9.55 - time) * 3)) : 0
    ;(birth.line.material as THREE.LineBasicMaterial).opacity = callOpacity * .32
    ;(birth.head.material as THREE.SpriteMaterial).opacity = callOpacity * .55
    ;(birth.label.material as THREE.SpriteMaterial).opacity = callOpacity * .68

    birth.glyphs.forEach(glyph => {
      const reveal = smooth((time - 9.15 - glyph.delay) / .24)
      const progress = smoother((time - 10.05 - glyph.delay) / 1.05)
      glyph.sprite.visible = active && reveal > 0 && progress < 1
      if (!glyph.sprite.visible) return
      const rotation = (time - 9.15) * .32
      const cos = Math.cos(rotation)
      const sin = Math.sin(rotation)
      const x = glyph.origin.x * cos + glyph.origin.z * sin
      const z = -glyph.origin.x * sin + glyph.origin.z * cos
      const spiral = Math.sin(progress * Math.PI)
      glyph.sprite.position.copy(target)
        .add(new THREE.Vector3(x * (1 - progress), glyph.origin.y * (1 - progress), z * (1 - progress)))
        .addScaledVector(glyph.orbit, spiral)
        .addScaledVector(glyph.end, progress)
      ;(glyph.sprite.material as THREE.SpriteMaterial).opacity = reveal * (1 - smooth((progress - .45) / .55)) * .9
      const scale = .4 + reveal * .15 + spiral * .05
      glyph.sprite.scale.set(scale, scale, 1)
    })
  }

  private updateScale(time: number) {
    const expansion = smoother((time - 18) / 12)
    const collapse = smoother((time - 34) / 8)
    this.clusters.children.forEach((object, index) => {
      const galaxy = object as THREE.Points
      ;(galaxy.material as THREE.PointsMaterial).opacity = (this.lightTheme ? .22 : .55) * smooth((time - 19 - index * 1.2) / 7) * (1 - smooth((time - 39) / 3))
      galaxy.rotation.y = time * (.012 + index * .003)
    })
    this.universe.scale.setScalar(time >= 42 ? 1 : Math.max(.001, 1 - collapse))
    this.universe.rotation.y = time * .006 + collapse * 3.8
    this.universe.rotation.z = collapse * .7
    this.camera.position.z = this.baseCameraZ + expansion * 96 * (1 - collapse)
    this.camera.position.y = 7.5 + expansion * 11 * (1 - collapse) - this.pointer.y * .65
  }

  private updatePhase(time: number) {
    const phase = time < 1.1 ? '奇点' : time < 5 ? '爆散' : time < 8 ? '聚合' : time < 11.65 ? '调用 → 创建' : time < 18 ? '恒星系' : time < 34 ? '星系群' : time < 42 ? '坍缩' : '奇点'
    if (phase !== this.phase) { this.phase = phase; this.onPhase(phase) }
  }

  setPointer(x: number, y: number) { this.pointerTarget.set(x, y) }

  setLight(value: boolean) {
    this.lightTheme = value
    ;(this.stars.material as THREE.PointsMaterial).opacity = value ? .26 : .8
    this.bloom.strength = value ? .32 : .48
    this.render()
  }

  resize(width: number, height: number) {
    const aspect = Math.max(1, width) / Math.max(1, height)
    this.camera.aspect = aspect
    this.baseCameraZ = aspect < 1 ? 39 : aspect < 1.45 ? 35 : 32
    this.home.set(aspect > 1.55 ? 8 : aspect > 1.1 ? 3.5 : 0, aspect > 1.55 ? -.3 : aspect > 1.1 ? 0 : -4.5, 0)
    this.universe.position.copy(this.home)
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(Math.max(1, width), Math.max(1, height), false)
    this.composer.setSize(Math.max(1, width), Math.max(1, height))
    this.render()
  }

  replay() {
    this.time = 0
    this.update(0)
    this.composer.render()
  }

  showAt(time: number) {
    this.time = Math.max(0, Math.min(DURATION - .01, time))
    this.update(0)
    this.composer.render()
  }

  showComplete() {
    this.showAt(25)
  }

  setActive(active: boolean) {
    if (this.active === active) return
    this.active = active
    cancelAnimationFrame(this.frame)
    if (active) {
      this.last = performance.now()
      this.frame = requestAnimationFrame(this.animate)
    }
  }

  private animate = (now: number) => {
    if (!this.active) return
    const delta = Math.min((now - this.last) / 1000, .05)
    this.last = now
    this.time += delta
    this.update(delta)
    this.composer.render()
    this.frame = requestAnimationFrame(this.animate)
  }

  private update(delta: number) {
    const time = this.time % DURATION
    this.starMaterial.uniforms.uTime.value = time
    this.updatePhase(time)
    this.updatePrimary(time)
    this.updateAccretion(time)
    this.updateBodies(time, delta)
    this.updateCallBirth(time)
    this.updateMinorInstances(time)
    this.updateComets(time)
    this.updateScale(time)
    this.galaxy.rotation.y = time * .014
    this.stars.rotation.y = time * .0015
    this.pointer.lerp(this.pointerTarget, 1 - Math.pow(.001, delta))
    this.camera.position.x = this.pointer.x * 1.1 + Math.sin(time * .055) * .18
    this.camera.lookAt(this.home.x * .25, 0, 0)
  }

  render() {
    this.update(0)
    this.composer.render()
  }

  dispose() {
    this.setActive(false)
    const geometries = new Set<THREE.BufferGeometry>()
    const materials = new Set<THREE.Material>()
    const textures = new Set<THREE.Texture>()
    this.scene.traverse(object => {
      if ('geometry' in object && object.geometry instanceof THREE.BufferGeometry) geometries.add(object.geometry)
      if ('material' in object) {
        const list = Array.isArray(object.material) ? object.material : [object.material]
        list.forEach((material: THREE.Material) => {
          materials.add(material)
          Object.values(material).forEach(value => { if (value instanceof THREE.Texture) textures.add(value) })
        })
      }
    })
    geometries.forEach(geometry => geometry.dispose())
    materials.forEach(material => material.dispose())
    textures.forEach(texture => texture.dispose())
    this.composer.dispose()
    this.renderer.dispose()
  }
}
