<script setup lang="ts">
import EngineeringMedia from '../components/EngineeringMedia.vue'
import EngineeringDiagram from '../components/EngineeringDiagram.vue'
import { onBeforeUnmount, onMounted, ref } from 'vue'

const activeId = ref('boot')
const tocOpen = ref(false)

function syncActiveChapter() {
  let current = 'boot'
  for (const chapter of CHAPTERS) {
    const section = document.getElementById(chapter.id)
    if (section && section.getBoundingClientRect().top <= 170) current = chapter.id
  }
  activeId.value = current
}

function selectChapter(id: string) {
  activeId.value = id
  tocOpen.value = false
}

onMounted(() => {
  syncActiveChapter()
  window.addEventListener('scroll', syncActiveChapter, { passive: true })
})
onBeforeUnmount(() => window.removeEventListener('scroll', syncActiveChapter))

const CHAPTERS = [
  { id: 'boot', no: '01', title: '引导与热更新', en: 'Loader & Watcher' },
  { id: 'voice', no: '02', title: '实时语音链路', en: 'Streaming Voice' },
  { id: 'pi', no: '03', title: '单工具 Agent', en: 'Pi & IPython' },
  { id: 'xml', no: '04', title: 'XML Agent', en: 'Embodied Interaction' },
  { id: 'moss', no: '05', title: 'Moss', en: 'Mindflow & Moment' },
  { id: 'dag', no: '06', title: 'DAG 编排', en: 'Dependency Scheduling' },
  { id: 'skills', no: '07', title: 'Skill 系统', en: 'Capability Packages' }
] as const
</script>

<template>
  <div class="eng">
    <header class="docs-masthead">
      <div class="docs-masthead-in">
        <div class="docs-breadcrumb"><span>ZERO-HARNESS</span><i>/</i><strong>工程实现</strong></div>
        <h1>工程实现</h1>
        <p>从引导加载、实时语音到 Agent、DAG 与 Skill，以源码架构图、真实调用链和失败边界记录当前系统。</p>
        <div class="docs-meta"><span>7 个章节</span><span>8 张源码架构图</span><span>11 张运行截图</span></div>
      </div>
    </header>

    <div class="docs-layout">
      <aside class="eng-toc" aria-label="工程实现章节目录">
        <button class="toc-toggle" type="button" :aria-expanded="tocOpen" @click="tocOpen = !tocOpen">
          <span>文档目录</span><span>{{ tocOpen ? '−' : '+' }}</span>
        </button>
        <div class="toc-desktop-title"><span>工程实现</span><small>ENGINEERING NOTES</small></div>
        <nav :class="{ open: tocOpen }">
          <RouterLink
            v-for="chapter in CHAPTERS"
            :key="chapter.id"
            :class="{ active: activeId === chapter.id }"
            :to="{ path: '/engineering', hash: `#${chapter.id}` }"
            @click="selectChapter(chapter.id)"
          >
            <span class="mono">{{ chapter.no }}</span>
            <div><strong>{{ chapter.title }}</strong><small>{{ chapter.en }}</small></div>
          </RouterLink>
        </nav>
      </aside>

      <main class="eng-document">
        <div class="docs-intro">
          <strong>阅读说明</strong>
          <p>本页记录 zero-harness 的当前工程实现。架构图来自真实源码中的组件、topic、类与调用关系，运行截图来自实际界面；语音部分区分独立语音服务、zero-rs 与 Moss 自有链路，参考原版 Moss 的设计也在对应章节注明来源。</p>
        </div>
        <section id="boot" class="eng-section">
          <header class="eng-chapter-head">
            <span class="chapter-no mono">01</span>
            <div><span class="eyebrow">Loader & Watcher</span><h2>Loader 引导与 Watcher 热更新</h2></div>
          </header>
          <p class="chapter-lede">启动不是扫描一整棵源码树，而是从明确清单建立注册表；更新也不是替换一个类对象就结束，而是让注册定义重新收敛。</p>
          <EngineeringDiagram name="boot" />
          <div class="eng-copy">
            <h3>启动路径：先确定事实源，再允许业务导入</h3>
            <p><code>app.main</code> 通过命令行参数或 <code>ZERO_ROUTINES_YAML</code> 调用 <code>set_yaml_path()</code>。此后 Loader 与 Watcher 都只从 <code>yaml_path()</code> 读取，避免“加载 A 清单、监听 B 清单”的静默分叉。<code>check_yaml()</code> 在 kernel 创建前检查文件、YAML 结构、非空条目和路径存在性，但此阶段刻意不 import 业务模块。</p>
            <p>kernel 启动后，唯一静态注册的 passive <code>RoutinesLoader</code> 被自动拉起。文件条目导入单个模块，目录条目只导入包的 <code>__init__.py</code>；两条路径最终都只收集 <code>__all__</code> 中公开、具名且非抽象的 Routine 子类。passive 条目的 kwargs 被覆盖式写入 <code>PassiveConfig</code>，随后逐类 <code>register_routine()</code> 并等待 kernel 回执。</p>
            <div class="eng-detail-grid">
              <div><span class="detail-index">01</span><h4>监听集</h4><p>目录条目展开包内各层 <code>.py</code> 与各层 <code>__init__.py</code>，跳过测试、缓存和下划线目录。文件删除保留 mtime=0 墓碑，使文件重建可以自动恢复。</p></div>
              <div><span class="detail-index">02</span><h4>重载顺序</h4><p>子模块先、祖先包后。因为 manifest 位于包的 <code>__init__.py</code>，只 reload 子模块不会刷新包命名空间里的 re-export 绑定。</p></div>
              <div><span class="detail-index">03</span><h4>变更语义</h4><p>新增导出注册、撤下导出注销、定义文件变化的类 reload；仅修改 manifest 不会让未变化的兄弟 Routine 全部重启。</p></div>
              <div><span class="detail-index">04</span><h4>失败恢复</h4><p>导入或 manifest 处于非法中间态时保留旧 mtime，下一轮 poll 自动重试；专属单线程 executor 避免磁盘 stat 阻塞 asyncio 主循环。</p></div>
            </div>
            <h3>源码变化如何变成运行态变化</h3>
            <p>Watcher 每秒扫描 mtime，发现变化后等待 500ms 去抖，再进入 <code>_reload_sources()</code>。YAML 自身的变化走另一条差分路径：新增条目注册、移除条目注销、kwargs 变化重新注入并 reload。源码 reload 后，新旧 manifest 做集合差分，并再次按 YAML 条目 cone 过滤，防止文件仍在但已不再公开的实施“幽灵注册”。</p>
            <pre class="eng-code"><code>文件变化 → mtime 差分 → debounce
         → reload(子模块 … 祖先包) → __all__ 差分
         → kwargs 重注入 → hub.reload_routine(new class)
         → kernel 停旧实例 → passive auto-start 新实例</code></pre>
            <div class="eng-boundary"><strong>明确边界</strong><p>当前没有通用 Python 依赖图，共享 helper 的变化不会自动推导所有受影响 Routine，需要用手动 <code>routine/reload</code> 指定 YAML 条目。reload 会停止目标旧实例；外部已经持有的旧 Python 类引用不会被自动改写。</p></div>
            <div class="eng-paths"><span>实现锚点</span><code>zero-py/app/routines/loader.py · set_yaml_path / check_yaml / _collect / RoutinesLoader</code><code>zero-py/app/routines/watcher.py · _watch_loop / _apply_yaml_change / _reload_sources</code></div>
          </div>
          <div class="eng-run-evidence">
            <span class="mono">RUN EVIDENCE · PI AGENT CREATOR</span>
            <h3>现场写一个新能力：<code>deep_research</code></h3>
            <p>用户只提出“现场写一个新能力”。Pi 通过 <code>ipython</code> 检查现有能力与 Routine SDK，将 <code>web_search</code>、<code>fanout_search</code>、<code>web_fetch</code> 等组合成 <code>dynamic/deep_research.py</code>。Watcher 热注册后，Agent 实际运行两轮、修正抓取问题，并确认 <code>routine/doc</code> 与 <code>routine/list</code> 已能看到新能力。</p>
          </div>
          <div class="eng-screenshots">
            <EngineeringMedia
              kind="image"
              title="01 · 从一句话开始创建"
              note="用户提出“现场写一个新能力”后，Pi 通过 ipython 读取 Routine SDK、检索 dynamic 目录与现有能力，进入创建流程。点击图片可查看未裁剪的完整界面。"
              asset="public/demo/engineering/pi-creator-01.webp"
              src="./demo/engineering/pi-creator-01.webp"
              href="./demo/engineering/pi-creator-01-full.webp"
              alt="Pi Agent 接到“现场写一个新能力”的要求后，通过 ipython 检索 Routine SDK、dynamic 目录和现有能力"
            />
            <EngineeringMedia
              kind="image"
              title="02 · 热注册、自验并交付调用方式"
              note="Agent 给出 deep_research 的组合流程、两轮真实运行、watcher 热注册和 run_routine 调用示例。当前位于 dynamic 实验场；转为正式能力仍需移入正式包并加入 routines.yaml。"
              asset="public/demo/engineering/pi-creator-02.webp"
              src="./demo/engineering/pi-creator-02.webp"
              href="./demo/engineering/pi-creator-02-full.webp"
              alt="deep_research 已创建并完成两轮运行，Watcher 已热注册，可通过 run_routine 调用"
            />
          </div>
        </section>

        <section id="voice" class="eng-section">
          <header class="eng-chapter-head">
            <span class="chapter-no mono">02</span>
            <div><span class="eyebrow">Streaming Voice</span><h2>独立语音服务与 Rust 流式合成</h2></div>
          </header>
          <p class="chapter-lede">本章记录 sherpa-onnx/services 的听觉编排和 zero-rs 的说话链。它们是独立语音实现，不是 Moss 当前的 ASR／TTS 接线；Moss 自有语音面见第 05 章。</p>
          <EngineeringDiagram name="voice" />
          <div class="eng-copy">
            <h3>听觉平面：微服务只处理能力，orch-rs 独占业务状态机</h3>
            <p><code>micserver-rs</code> 以 100ms 块发布带序号的 PCM；<code>kws-rs</code> 与板载 <code>kwsxf-rs</code> 都能发布 <code>voice.wake</code>，编排器用 0.5s 窗口合并双引擎对同一次人声的报告。<code>vad-rs</code> 提供人声边沿，<code>asrserver-rs</code> 按 <code>sid</code> 管理识别会话并发布 started、partial、endpoint、final、round_done 等事件。各服务经 broker 的 XSUB/XPUB 代理互联，协议是“topic 前缀 + JSON 或二进制载荷”的单帧消息。</p>
            <p><code>orch-rs</code> 不实现唤醒、VAD 或 ASR，而是把三种策略展开成能力组合：<code>single</code> 由唤醒开轮，<code>continuous</code> 在窗口内允许唤醒或 VAD 边沿连续开轮，<code>manual</code> 只接受外部 start/stop。内部 <code>Phase</code> 明确区分 Idle、Starting、Recording、Finishing；收轮请求若早于 started，会先记下 <code>stop_after_start</code>，等 sid 回填后再 finish，避免发出空 sid。</p>
            <div class="eng-table-wrap">
              <table class="eng-spec-table">
                <thead><tr><th>策略</th><th>开轮触发</th><th>收轮依据</th><th>窗口与保险丝</th></tr></thead>
                <tbody>
                  <tr><td>single</td><td>唤醒词</td><td>服务端 endpoint 或本地静音；无话可 abort</td><td>单轮，默认上限 15s</td></tr>
                  <tr><td>continuous</td><td>唤醒词；窗口内 VAD 上升沿</td><td>endpoint / 静音 / 窗口或轮次超时</td><td>连续窗口 180s，句首回滚 10 块</td></tr>
                  <tr><td>manual</td><td><code>voice.orch.cmd start</code></td><td>仅外部 stop</td><td>不跑 VAD，90s 保险丝</td></tr>
                </tbody>
              </table>
            </div>
            <h3>播放与回声：参考流是一条独立数据面</h3>
            <p><code>playback-rs</code> 接收 <code>voice.play.src</code>，按 100ms 窗混音、重采样并持续驱动声卡，同时把喇叭实播镜像发布为 <code>voice.play.mix</code>。<code>aecserver-rs</code> 以这份参考和原始麦克风流做 render/capture 对齐，播放期间启用 AEC3 与噪声抑制，并发布 <code>voice.aec.evt active</code>。orch-rs 用 active 状态抑制播报期间的 local 自唤醒；这不是笼统的“全双工开关”，而是一条具体的防自唤醒反馈链。</p>
            <div class="eng-boundary"><strong>AEC 输出与识别输入分离</strong><p>当前 <code>asrserver-rs</code>、<code>vad-rs</code>、<code>kws-rs</code> 订阅的都是 <code>voice.audio.raw</code>，不是 <code>voice.audio.aec</code>。因此架构图保留 AEC 的处理与状态反馈，但不把“识别使用消回声后的音频”写成既成事实。</p></div>
            <h3>说话平面：Routine 实例与进程级 TTS actor 分离</h3>
            <p>zero-rs 的每个 <code>speak</code> 命令实例持有独立 token，并声明 <code>audio_mouth</code> 模块互斥。文本通过 <code>Feed</code> 增量进入 <code>TtsService</code>，<code>_eof</code> 转成 <code>Finish</code>，停止则转成 <code>Cancel</code>。进程级 actor 复用一条 Seed 双向 WebSocket，按实例 FIFO 串行消化命令；只有收到 <code>WaitDone</code> 的 started 实例才真正建立会话和合成，避免下一段语音越过调度提前出声。</p>
            <p>PCM 经 typed channel 送入 <code>AudioService</code> 边收边播；Finish 之后还要等设备 drain 才完成。即使已经进入 Finalizing，Cancel 仍然能够停播放并返回 interrupted。建连、发文本、收帧与播放等待都有超时保护，避免单任务 actor 被半死连接永久卡住。情绪不在创建时固化：<code>speak</code> 经 <code>agent/state</code> 采样 <code>tts_instruction</code>，actor 在首个 Feed 建会话前读取活单元现值。</p>
            <pre class="eng-code"><code>voice.wake → orch open_round → asr.cmd start → asr.evt started
             → endpoint / silence / manual stop → asr.cmd finish
             → final → orch.evt result → round_done → idle / continuous

LLM text → speak(Feed*) → Seed session → PCM → AudioService
         → _eof / Finish → drain → SpeakOutcome
         → stop / Cancel → session cancel + playback stop</code></pre>
            <div class="eng-boundary"><strong>装配边界</strong><p><code>sherpa-onnx/services</code> 的 playback-rs 与 zero-rs 内部的 AudioService 是两条播放实现，并未默认桥接；Moss 也不订阅这里的 <code>voice.*.evt</code>，且不调用 zero-rs <code>speak</code>。双向流式 TTS 不自动等于完整听说同时全双工，各条链路的识别、取消与停播由自身实现决定。</p></div>
            <div class="eng-paths"><span>语音服务实现</span><code>sherpa-onnx/services/{orch-rs,asrserver-rs,micserver-rs,aecserver-rs,playback-rs}/src/</code><code>sherpa-onnx/services/INTEGRATION.md · voice.toml · bus-config/src/lib.rs</code><span>zero-harness 实现</span><code>zero-rs/src/routines/speak/{mod.rs,tts.rs,seed.rs,protocol.rs}</code><code>zero-rs/src/audio/service.rs</code></div>
          </div>
          <EngineeringMedia
            kind="image"
            title="Bus Monitor：从唤醒到识别结果的完整事件链"
            note="single 策略下，voice.wake 触发 ASR / VAD start，orch 进入 recording；随后可见 started、partial、endpoint、final、round_done，orch 发布 result 并回到 idle。图中还保留一次 no_speech → aborted，完整呈现独立听觉服务的事件链与状态收束。"
            asset="public/demo/engineering/voice-bus-monitor.webp"
            src="./demo/engineering/voice-bus-monitor.webp"
            href="./demo/engineering/voice-bus-monitor-full.webp"
            alt="语音服务 Bus Monitor 中的唤醒、VAD、ASR 与编排事件，包括识别完成和无语音中止"
          />
        </section>

        <section id="pi" class="eng-section">
          <header class="eng-chapter-head">
            <span class="chapter-no mono">03</span>
            <div><span class="eyebrow">Pi & IPython</span><h2>单工具 Agent：Pi 与 IPython</h2></div>
          </header>
          <p class="chapter-lede">Pi 将模型直接看到的工具面收敛为 <code>ipython</code>；代码在持久 Python 环境中执行，再通过桥接能力进入共享 Routine 系统。</p>
          <EngineeringDiagram name="pi" />
          <div class="eng-copy">
            <h3>包装层不重写 Agent 引擎</h3>
            <p><code>PiAgent</code> 是一个 Routine 外壳，真正的会话循环、上下文压缩、多模型适配、重试与 steering 都交给 <code>AgentSession</code>。外壳负责四件事：建立 workspace 与资源加载器、注入唯一 custom tool、把 SDK 事件翻译成 zero 前端协议，以及在 Routine 停止时 abort 并 dispose 会话。这样模型能力升级不需要在 zero-ts 再维护一套通用 AgentLoop。</p>
            <p>初始化时，共享模型配置被解析为 SDK 可用的 model runtime；<code>SessionManager</code> 把每个 Agent 的 JSONL 会话文件固定到自身 workspace。<code>DefaultResourceLoader</code> 被收窄，只暴露 workspace 下 <code>.pi/skills</code> 的内容，避免把用户机器上其他 Agent 的 Skill 全部带入提示词。</p>
            <div class="eng-detail-grid">
              <div><span class="detail-index">输入</span><h4>prompt / abort / history</h4><p>web_server 请求经就绪门闩进入 session，避免 create 后立即 chat 时初始化尚未完成而丢消息。</p></div>
              <div><span class="detail-index">输出</span><h4>三类前端事件</h4><p>assistant_output 承载增量文本，feedback 承载工具运行与结果，usage 回传统计；messageId 区分同轮多条消息。</p></div>
              <div><span class="detail-index">中断</span><h4>epoch + AbortSignal</h4><p>打断后递增 epoch，旧消息的陈旧 delta 被丢弃；停止信号继续传到底层 IPython Routine，不只停前端渲染。</p></div>
              <div><span class="detail-index">恢复</span><h4>JSONL 会话回放</h4><p>resume 恢复模型对话和上下文，不重放此前工具执行；工作区、会话文件和 Agent 身份保持一一对应。</p></div>
            </div>
            <h3>唯一工具不是能力上限，而是稳定 ABI</h3>
            <p><code>makeIpthonTool()</code> 只声明 <code>code</code>、<code>timeout</code>、<code>reset</code> 三个参数。执行时附加 <code>from_agent_id</code>，通过 <code>callStoppable('ipython', …, signal)</code> 路由到 Python 侧。工具结果同时支持 text 与 image block：IPython 的 display 捕获可以转成模型直接可见的图像，前端则把图片持久化后用 <code>/files</code> 路径展示。</p>
            <p>模型在持久命名空间里写 Python，变量、导入与生成对象可以跨调用存在。需要进入共享系统时，代码使用显式的 <code>run_routine()</code> 桥接函数；新增 Routine 不要求修改 Pi 的工具 schema。由此形成“模型面对一个入口，宿主保留整张能力网络”的结构。</p>
            <pre class="eng-code"><code>model → ipython { code, timeout, reset }
      → PiAgent.callStoppable → kernel → IPython.py
      → 持久 namespace / display / interrupt
      → run_routine(name, kwargs) → 任意共享 Routine
      → text + image toolResult → AgentSession → 下一轮模型输入</code></pre>
            <div class="eng-boundary"><strong>关键区别</strong><p>“单工具”只描述模型可见的 custom tool 数量，不表示系统只能运行 Python，也不意味着工具执行在 TypeScript 进程内。真正执行位于 Python Routine，调用和取消都跨 kernel 传递。</p></div>
            <div class="eng-paths"><span>实现锚点</span><code>zero-ts/app/routines/user/agents/pi/agent.ts · PiAgent / _init / _onPiEvent</code><code>zero-ts/app/routines/user/agents/pi/tools.ts · makeIpthonTool</code><code>zero-py/app/routines/user/ipython/IPython.py · 持久内核与 run_routine 桥</code></div>
          </div>
          <EngineeringMedia
            kind="image"
            title="一个 ipython 入口，创建并调用共享 Routine"
            note="与第 01 章同一次真实 Creator 会话：模型通过 ipython 检索能力、编写 deep_research，再用 run_routine 完成调用自验，展示单工具 ABI 与共享能力桥接。"
            asset="public/demo/engineering/pi-creator-02.webp"
            src="./demo/engineering/pi-creator-02.webp"
            href="./demo/engineering/pi-creator-02-full.webp"
            alt="Pi 通过 ipython 创建 deep_research，并给出 run_routine 的真实调用与验证结果"
          />
        </section>

        <section id="xml" class="eng-section robot-chapter">
          <header class="eng-chapter-head">
            <span class="chapter-no mono">04</span>
            <div><span class="eyebrow">XML Agent · 机器人交互</span><h2>XML Agent：机器人流式交互</h2></div>
          </header>
          <p class="chapter-lede">XML Agent 面向机器人持续输入与即时行动：模型字流增量进入 Act，行动随解析发生；新感知到来时，Reflex 决定通过、打断、延后或丢弃。</p>
          <EngineeringDiagram name="xml" />
          <div class="eng-copy">
            <h3>常驻 Agent 与每轮 Act 是两个生命周期</h3>
            <p><code>XmlAgent</code> 是由 manager 创建的动态常驻实例，持有 agent_id、session_id、当前模型、事件锁、epoch 和当前 Act 句柄。它的 <code>run()</code> 不执行固定问答循环，而是驻留等待 stop；每次 <code>chat_message</code> 驱动一个 react task。SQLite Memory 与可选 OpenViking 由 <code>XmlContextProvider</code> 统一封装，session workspace 同时保存 Skill 与恢复游标。</p>
            <p>每轮 react 只创建一个 <code>Act</code> 子实例。模型流式 TextDelta 通过 <code>ctx.send</code> 喂入 Act 的 body 队列，XML parser 逐步产出 TextChunk、ChildOpen、ChildBody、ChildClose；body_shell 再把标签转换成真正的子 Routine。行动可以在模型整段输出完成前开始，这正是机器人低延迟交互的关键，而不是把 XML 当作最终回答的排版格式。</p>
            <div class="eng-detail-grid">
              <div><span class="detail-index">文本</span><h4>裸文本 → speak</h4><p>文本段首创建 speak，后续 chunk 持续发送，段尾只发 <code>_eof</code>；TTS 不可用时降级为纯文字，不让解析管线崩掉。</p></div>
              <div><span class="detail-index">标签</span><h4>ChildOpen → Routine</h4><p>显式别名先映射回注册名，再注入 <code>from_agent_id</code> 并创建子实例；单个 child scope 失败转为错误结果，不拖垮整轮。</p></div>
              <div><span class="detail-index">收束</span><h4>双条件退出</h4><p>只有 body_shell 已完成且 done_q 已排空，Act 的 async generator 才退出，避免模型后半段标签尚未派发就提前结束。</p></div>
              <div><span class="detail-index">历史</span><h4>执行事实回到会话</h4><p>工具结果经完成回调进入 done_q，再流式 yield 给 Agent；输入、模型输出与行动反馈由上下文层统一入史。</p></div>
            </div>
            <h3>Reflex 决定“现在听、稍后再听，还是停下听”</h3>
            <p>当 Act 在途时，新输入不直接覆盖当前 react。<code>ReflexChain</code> 先经 state_gate 获取当前状态，再由 Jev 结合正在说的话和新输入做决策；判定期间到达的消息进入 pending，并在需要时合并重判。INTERRUPT 走 epoch 递增、react 取消与 Act 停止，DEFER 则暂存到 drain 时刻再落库开新轮。由于停止 Act 会触发 Routine 父子级联，机器人说话与设备动作才有机会同步收束。</p>
            <pre class="eng-code"><code>idle input → store incoming → create Act → LLM stream
active input → ReflexChain → PASS / INTERRUPT / DEFER
INTERRUPT → epoch++ → cancel react → stop Act → stop speak / car/* 子树
LLM delta → Act parser → body_shell → Routine result → done_q → Agent</code></pre>
            <div class="eng-boundary"><strong>行动与打断是两个观察面</strong><p>下方历史会话截图展示 XML 动作标签与真实设备回执；Reflex 的打断语义则由 epoch 递增、react 取消、Act 停止和父子级联回收共同完成，被中断的部分输出也会按实际执行结果进入后续历史。</p></div>
            <div class="eng-paths"><span>实现锚点</span><code>zero-py/app/routines/user/xml/agent/agent.py · XmlAgent / react / interrupt</code><code>zero-py/app/routines/user/xml/agent/{reflex.py,jev.py,provider.py}</code><code>zero-py/app/routines/user/xml/{act.py,xml_routine.py,xml_parser.py}</code></div>
          </div>
          <div class="eng-screenshots">
            <EngineeringMedia
              kind="image"
              title="01 · XML 字流中的动作标签与真实执行回执"
              note="历史机器人会话中，模型在同一轮输出 play_music 与三个 shake 标签；下方展开三次 car/shake 的 done 回执及 stopped/interrupted 字段，呈现流式行动与结果返回。"
              asset="public/demo/engineering/xml-stream-actions.webp"
              src="./demo/engineering/xml-stream-actions.webp"
              href="./demo/engineering/xml-stream-actions-full.webp"
              alt="XML Agent 输出 play_music 和三个 shake 标签，并显示三次 car shake 的完成回执"
            />
            <EngineeringMedia
              kind="image"
              title="02 · 连续 ASR 输入中的问答与设备行动"
              note="同一会话里，多轮 from_asr 输入与回答连续出现；play_music + shake 和 list_music 调用均到达 done，呈现真实语音输入下的连续 XML 行动链。"
              asset="public/demo/engineering/xml-continuous-interaction.webp"
              src="./demo/engineering/xml-continuous-interaction.webp"
              href="./demo/engineering/xml-continuous-interaction-full.webp"
              alt="XML Agent 连续接收 ASR 输入，完成音乐、摇头动作和音乐列表调用"
            />
          </div>
        </section>

        <section id="moss" class="eng-section robot-chapter">
          <header class="eng-chapter-head">
            <span class="chapter-no mono">05</span>
            <div><span class="eyebrow">Moss · 机器人交互</span><h2>Moss：Mindflow、Moment 与连续交互</h2></div>
          </header>
          <p class="chapter-lede">Moss 不把交互压缩成固定问答轮次。Mindflow 决定注意力与续拍，Ghost 组织单拍思考，Moment 缝合历史，Shell 将结构化输出送入行动。</p>
          <EngineeringDiagram name="moss" />
          <div class="eng-copy">
            <h3>自有语音面：参考并适配原版 Moss</h3>
            <p>当前 <code>app/moss.yaml</code> 装配的是 <code>mossv2/asr</code> 与 <code>commands/say.py</code>。听觉器官自己用 sounddevice 采集并直连豆包 SAUC WebSocket，经 ListenerController 完成 onset、判停与交付，再进入 <code>mindflow.declare</code>；它不订阅第 02 章的 <code>voice.*.evt</code>。说话也不调用 zero-rs <code>speak</code>，而是由 say 使用 Python 公共 tts 包的 SeedTTS 与 LocalSpeaker。</p>
            <p>这条链路的听觉分层、onset／stop／deliver、Recognizer.commit、流式说话、截声与执行回执等设计，参考并适配自原版 <code>MOSShell</code>。当前实现将原版宿主装线改造成 Routine、Mindflow 与 CTML／Channel 协作，并加入 ptt 等现有语义；语音设计的参考来源在此明确保留。</p>
            <EngineeringDiagram name="mossVoice" />
            <div class="eng-detail-grid">
              <div><span class="detail-index">听觉</span><h4>采集与识别是一个完整器官</h4><p>Ear 负责能量门、有界队列、断线重连、静音收流与判停尾包；partial 主要用于起话和诊断，utterance 进入听侧策略。</p></div>
              <div><span class="detail-index">交付</span><h4>FIRST 与 TAIL 使用同一 id</h4><p>ListenerController 把起话、判停、交付分开；commit 后收识别流，再由 Mindflow 吸收同一段的首尾包，避免同一句话重复开拍。</p></div>
              <div><span class="detail-index">说话</span><h4>文本流与真实播放同时收束</h4><p>say 的 body 增量直接 feed，eof 调 finish；LocalSpeaker 边收边播，停止时截声并取消合成，执行回执记录正常说完或被截断。</p></div>
              <div><span class="detail-index">闸门</span><h4>当前默认是听说半双工</h4><p><code>mute_on_say=true</code> 时，say 发布 speech start／stop，ASR 在出声期间丢麦克风帧以避免自听回灌；这不是听说同时全双工。</p></div>
            </div>
            <h3>Mindflow：先仲裁注意力，再决定是否调用模型</h3>
            <p>外部世界通过 <code>mindflow.declare</code> 申报 Impulse，而不是直接请求 LLM。每个来源由 Nucleus 提供默认优先级、聚合方式与冷却策略，随后进入统一检录缓冲。裁判循环被新信号或链终点亮，先做同源聚合与 rank，再让头号挑战者挑战当前 Attention；priority、strength、保护期、强度衰减、同 id 尾包吸收和失败处置都在这一层完成。</p>
            <p>仲裁赢家形成一条单源拍链。<code>next</code> 可以强制当前链续拍，普通动作回执只入史、不自动触发下一拍；错误帧、动作 meta 的 <code>observe</code> 或调用处的 <code>observe="true"</code> 会形成 owed 注意债，才明确要求灵魂继续观察。由此避免机器人每完成一个微小动作就无条件多调用一次模型。</p>
            <div class="eng-table-wrap">
              <table class="eng-spec-table">
                <thead><tr><th>入口／模式</th><th>作用</th><th>失败或后续语义</th></tr></thead>
                <tbody>
                  <tr><td>default</td><td>参与注意力竞争</td><td>挑战失败回检录缓冲，下届独立重赛</td></tr>
                  <tr><td>notify / aside</td><td>只送达，不接管当前注意力</td><td>挂账进入后续拍，不丢消息</td></tr>
                  <tr><td>next</td><td>送达并强制当前链续拍</td><td>挂账且插队</td></tr>
                  <tr><td>knock / command</td><td>门前通知／CTML 反射弧</td><td>失败即丢；command 不经过模型</td></tr>
                  <tr><td>interrupt + effort</td><td>停身体与是否回答分成两个轴</td><td>interrupt 停旧 logos；effort 决定开脑还是只留空拍</td></tr>
                </tbody>
              </table>
            </div>
            <h3>Ghost 与 Moment：把碎片世界缝成一拍</h3>
            <p>一次 <code>Ghost.think</code> 开启一个 Moment。<code>previous</code> 保存上一拍真实执行回执和停止原因，<code>percepts</code> 按来源保存本拍输入，<code>dynamic_context</code> 保存同源覆盖的当前状态，<code>logos</code> 保存本拍实际输出。被中断的拍也提交已经执行的部分，防止后续历史把未发生的完整回答当成事实。</p>
            <p>Moments 用帧滑窗限制历史长度，并可把旧帧压缩成 Epoch recap；瞬态只出现在当前模型视图，不进入持久历史。Brain 的 <code>input_items</code> 负责最终渲染，因此数据层始终保持结构，不把状态快照拼成无法区分的历史对话。</p>
            <h3>Shell 与 Channel：模型字流直接成为行动</h3>
            <p>BodyScanner 只解释当前一层，一个标签对应一个实例；<code>all</code>、<code>any</code>、<code>loop</code> 与普通动作没有解析器特权，差异由各自 Routine 如何解释 body 决定。唯一 route 服务负责词表与 lane 闸门、参数校验和编排注入，Channel 负责调度，容器逐层汇总回执。Tracer 记录真实执行事实，帧边界整批回灌，迟到回执带旧拍号即时补交。</p>
            <pre class="eng-code"><code>declare → Nucleus → pending → rank + challenge → Ghost.think
       → Moment(previous + percepts + transient) → Brain CTML stream
       → Shell BodyScanner → route → Channel / container → Routine
       → Tracer obs → Moment 入史；next / owed → 显式续拍</code></pre>
            <div class="eng-boundary"><strong>连续交互与听说双工分离</strong><p>下方截图使用 Moss 自己的 ASR／say 与 CTML 行动链，把代码创建、动作输出、观察回执和 Moment 帧对应起来。Attention 仲裁与 owed 续拍属于注意力编排；当前默认 <code>mute_on_say=true</code> 的听说半双工则是语音输入闸门，两者分别工作。</p></div>
            <div class="eng-paths"><span>当前 Moss 语音</span><code>zero-py/app/routines/user/mossv2/{asr/,listen/,commands/say.py}</code><code>zero-py/app/routines/user/tts/{seed/,audio_speaker/}</code><span>原版参考来源</span><code>MOSShell/src/ghoshell_moss/host/listener/</code><code>MOSShell/src/ghoshell_moss/core/speech/stream_tts_speech.py</code><span>实现锚点</span><code>zero-py/app/routines/user/mossv2/{mindflow.py,nucleus.py} · 申报与仲裁</code><code>zero-py/app/routines/user/mossv2/{ghost.py,brain.py,moment.py} · 一拍与帧链</code><code>zero-py/app/routines/user/mossv2/{shell.py,body.py,channel.py,container.py} · 行动与回执</code></div>
          </div>
          <div class="eng-screenshots">
            <EngineeringMedia
              kind="image"
              title="01 · CTML 输出、真实动作与观察回执"
              note="运行输出中，模型先通过 code 创建并注册 car_zigzag，再以 say 与 car:car_zigzag 组成行动；observe 回传注册结果以及三段、四段之字形运动结果，新的 ASR 输入随后进入下一拍。CTML、Routine 执行与回执在同一历史中顺序衔接。"
              asset="public/demo/engineering/moss-ctml-actions.webp"
              src="./demo/engineering/moss-ctml-actions.webp"
              href="./demo/engineering/moss-ctml-actions-full.webp"
              alt="Moss 创建并调用 car_zigzag，显示 say、CTML 动作标签和 observe 执行回执"
            />
            <EngineeringMedia
              kind="image"
              title="02 · Moments 帧链保留每一拍的输入与执行事实"
              note="帧链展开帧 15–23，逐帧并列 ASR 输入、logos 与执行回执：帧 20 记录代码创建，帧 21／22 记录 car_zigzag 调用与观察结果，顶部帧 23 保留待归账状态。帧链与行动输出可以逐拍对应。"
              asset="public/demo/engineering/moss-moments.webp"
              src="./demo/engineering/moss-moments.webp"
              href="./demo/engineering/moss-moments-full.webp"
              alt="Moss Moments 帧链展示代码创建、ASR 输入、logos、car_zigzag 动作与观察回执"
            />
          </div>
        </section>

        <section id="dag" class="eng-section">
          <header class="eng-chapter-head">
            <span class="chapter-no mono">06</span>
            <div><span class="eyebrow">Dependency Scheduling</span><h2>DAG：依赖驱动的 Routine 编排</h2></div>
          </header>
          <p class="chapter-lede">DAG 将 Routine 调用组织为显式依赖图：加载时校验结构，执行时持续寻找就绪节点，让可并行的分支独立推进。</p>
          <EngineeringDiagram name="dag" />
          <div class="eng-copy">
            <h3>定义期：把结构错误挡在执行之前</h3>
            <p><code>run_dag</code> 接收 DAG 名称或 YAML 路径，以及字符串形式的外部 inputs。loader 先构造 <code>DagSpec</code>，检查节点 id、依赖与输出引用、<code>$node.output</code>／<code>$WI.input</code> 引用、trigger_rule、重试字段和输入声明，再用 Kahn 算法检测环。运行入口随后校验必填输入并按 str、int、float、bool 强制转换，未声明的额外输入仍可透传。</p>
            <p>节点不仅声明 Routine 与 depends_on，还可以声明 <code>when</code>、<code>trigger_rule</code>、<code>retry</code> 和 <code>output_format</code>。因此“依赖已结束”和“依赖成功”是不同条件：all_done 可以在失败或跳过后继续，all_success 则要求成功；跳过本身也是终态，仍可能解锁下游。</p>
            <h3>执行期：完成事件驱动，而非分层批处理</h3>
            <p><code>try_dispatch()</code> 持续扫描 pending，凡是依赖全部终态的节点就立即处理参数并创建子实例。<code>create()</code> 只建命令，随后挂上 <code>on_stopped_handler</code> 再 <code>start()</code>；start 只等 started 回执，不等完成，所以多个独立分支可以同时在 running 集合中推进。子实例完成进入 done_q，主循环更新 outputs，再调用下一轮 try_dispatch。</p>
            <div class="eng-detail-grid">
              <div><span class="detail-index">并发</span><h4>不按拓扑层等待</h4><p>同一层节点不必一起完成；只要某个下游自己的依赖已终态，就能比其他分支更早启动。</p></div>
              <div><span class="detail-index">重试</span><h4>失败回到 pending</h4><p>在 max_attempts 内按 delay_ms 做指数退避，发布 retrying 事件，再把同一节点放回 pending。</p></div>
              <div><span class="detail-index">嵌套</span><h4>DAG 也是 Routine</h4><p>一个节点可以再次调用 run_dag；外层只看到普通子实例的输入、输出和停止，不需要第二套调度协议。</p></div>
              <div><span class="detail-index">观测</span><h4>dag_event 全轨迹</h4><p>run_started、节点 running/completed/failed/skipped/retrying 和 run_done 可还原真实执行过程。</p></div>
            </div>
            <h3>失败与取消：停止派发不等于运行中节点已经消失</h3>
            <p><code>dag_cancel</code> 是普通节点，只有其返回的取消哨兵会被调度器识别。取消后不再派发 pending，running 中尚未形成输出的节点在结果里标记为 skipped，并抛出 DAG cancelled。当前源码没有在这里逐个保留 handle 并等待在途节点全部结束；真正的子树回收依赖 RunDag 自身停止时的 Routine 级联语义。</p>
            <pre class="eng-code"><code>load_dag_yaml_file → DagSpec（引用校验 + Kahn 环检测）
pending --deps_all_terminal--> resolve_kwargs / should_run
        --create + on_stopped + start--> running
running --done_q--> outputs / retry / skipped --> try_dispatch
outputs --> dag.outputs 别名映射 或 全节点 state/output/error</code></pre>
            <div class="eng-boundary"><strong>取消不等于在途节点已经消失</strong><p>取消哨兵会终止调度循环，但运行中节点的结果被记为 skipped，不代表它们此刻都已执行完毕。判断整次子树是否收束，需要结合事件时间、命令实例树与父实例停止后的级联回收，不能只看 <code>dag_run_done</code>。</p></div>
            <div class="eng-paths"><span>实现锚点</span><code>zero-py/app/routines/user/dag/{loader.py,spec.py} · 静态结构</code><code>zero-py/app/routines/user/dag/{run_dag.py,executor.py} · 就绪集、重试与取消</code><code>zero-py/app/routines/user/dag/cancel.py · 普通 Routine 形式的取消节点</code></div>
          </div>
          <EngineeringMedia
            kind="image"
            title="approval_dag：草稿、审批与发布按依赖完成"
            note="真实 DAG Runner 完成态：draft、review、publish 三节点依次 done，review 返回 approved，publish 输出已发布。该图聚焦依赖推进与节点结果，不用同一示例泛化并发、重试或取消行为。"
            asset="public/demo/engineering/dag-approval.webp"
            src="./demo/engineering/dag-approval.webp"
            href="./demo/engineering/dag-approval-full.webp"
            alt="approval_dag 的 draft、review、publish 三节点完成，审批结果为 approved"
          />
        </section>

        <section id="skills" class="eng-section">
          <header class="eng-chapter-head">
            <span class="chapter-no mono">07</span>
            <div><span class="eyebrow">Capability Packages</span><h2>Skill：能力包与渐进披露</h2></div>
          </header>
          <p class="chapter-lede">Skill 是提示词与资源包系统，不是另一张 Routine 注册表。它通过摘要、按需加载与预载进入 Agent，再通过明确的桥接关系使用系统能力。</p>
          <EngineeringDiagram name="skills" />
          <div class="eng-copy">
            <h3>共享库是内容真源，工作区是 Agent 的使用副本</h3>
            <p><code>skill/fetch</code> 把 URL 内容安装到共享库 <code>~/.zero/skills</code>，<code>skill/repo/publish</code> 则把工作区版本发布回同一库。Agent 不默认装载整个库，而是用 <code>skill/repo/list</code> 查看候选，再以 <code>skill/repo/pull</code> 把需要的包复制到自己的 <code>skill_dir</code>。同版本默认跳过以保护本地修改，<code>force=True</code> 才覆盖。</p>
            <p><code>SkillRegistry</code> 是多目录扫描器，按目录顺序 first-wins 去重，读取 SKILL.md frontmatter 的 name、description、version 与 agents。profile 只影响清单过滤，显式 get/invoke 仍可按名读取；它自身没有会话状态，也不知道 kernel、agent_id 或项目根，工作区定位由 Agent 桥接层负责。</p>
            <div class="eng-detail-grid">
              <div><span class="detail-index">L1</span><h4>摘要渐进披露</h4><p>先只把名称与描述放进模型可见清单，降低无关 Skill 对系统提示词的长期占用。</p></div>
              <div><span class="detail-index">L2</span><h4>正文按需加载</h4><p><code>skill/load</code> 返回完整指令作为 tool result，保留“为何采用这些规则”的对话因果链。</p></div>
              <div><span class="detail-index">旁路</span><h4>full 预载</h4><p>创建 Agent 时把指定正文直接并入 system prompt，适合每次会话都必须遵循的基础手册。</p></div>
              <div><span class="detail-index">执行</span><h4>指令不等于能力注册</h4><p>模型阅读 Skill 后仍经 IPython、routine-bridge 或普通 Routine 调用进入真实执行面。</p></div>
            </div>
            <h3>两种 Agent 复用内容层，但装配方式不同</h3>
            <p>Pi 在初始化时把基础 <code>routine-bridge</code> 和显式指定的 Skill 复制到 <code>.pi/skills</code>；summary 级交给 SDK 的 available_skills 渐进加载，full 级正文直接拼入系统提示词。XML Agent 则按 <code>level1_skills</code> 注入摘要、按 <code>preload_skills</code> 注入全文，通用 <code>skill/load</code> 通过 <code>agent/state</code> 反查当前 Agent 的 skill_dir。</p>
            <p>Skill 管理入口本身仍是 Routine，由 RoutinesLoader 根据 YAML 和 <code>__all__</code> 注册；SKILL.md 内容包则由 SkillRegistry 扫描。两者共享文件路径和调用关系，却没有合并成一张注册表。<code>routine-bridge</code> 只是指导模型如何从 Python 环境进入共享 Routine 系统的内容包，不是另一个模块加载器。</p>
            <pre class="eng-code"><code>URL / workspace publish → ~/.zero/skills（共享库）
共享库 → repo/pull → workspace/skills（当前 Agent 副本）
副本 → list 摘要 → load 正文 → 模型指令
模型 → routine-bridge / IPython → run_routine → 共享执行面</code></pre>
            <div class="eng-boundary"><strong>在途修改口径</strong><p>共享库更新不会自动改写已经复制到 Pi 工作区的文件，也不会重写已经拼入 system prompt 的 full 预载内容；需要重新 pull 或重建会话。通用 Registry 可在再次 list/load 时重扫，但不能据此声称所有 Agent 的现有提示词会即时更新。</p></div>
            <div class="eng-paths"><span>实现锚点</span><code>zero-py/app/routines/user/skill/registry.py · SkillRegistry / build_registry</code><code>zero-py/app/routines/user/skill/{list.py,load.py,repo_pull.py,repo_publish.py,fetch.py}</code><code>zero-ts/app/routines/user/agents/pi/agent.ts · workspace Skill 装配</code><code>zero-py/app/routines/user/xml/agent/agent.py · level1 / preload Skill 装配</code></div>
          </div>
          <div class="eng-screenshots">
            <EngineeringMedia
              kind="image"
              title="01 · 共享库中选择摘要或全文"
              note="真实 Agent 创建界面通过 skill/repo/list 读取共享库；routine-bridge 选为摘要，routine-sdk 切换为全文。两种加载等级在同一选择器中明确可见。"
              asset="public/demo/engineering/skill-library-list.webp"
              src="./demo/engineering/skill-library-list.webp"
              href="./demo/engineering/skill-library-list-full.webp"
              alt="Skill 共享库列表，routine-bridge 选择摘要，routine-sdk 选择全文"
            />
            <EngineeringMedia
              kind="image"
              title="02 · 打开 routine-bridge 时按需读取正文"
              note="详情浮层通过 skill/repo/get 拉取真实 SKILL.md，展示 run_routine 的调用方式、发现 Routine 与读取文档等说明。内容层由此进入 IPython 与共享 Routine 执行面。"
              asset="public/demo/engineering/skill-load-body.webp"
              src="./demo/engineering/skill-load-body.webp"
              href="./demo/engineering/skill-load-body-full.webp"
              alt="routine-bridge Skill 的正文详情，包含 run_routine 调用方式和示例"
            />
          </div>
        </section>

        <nav class="docs-pager" aria-label="相邻章节">
          <RouterLink to="/robot"><small>上一页</small><span>← 机器人</span></RouterLink>
          <RouterLink to="/registry"><small>下一页</small><span>注册清单 →</span></RouterLink>
        </nav>
      </main>
    </div>
  </div>
</template>

<style scoped>
.eng {
  --doc-bg: #f5f6f8;
  --doc-paper: #ffffff;
  --doc-ink: #1d2735;
  --doc-body: #465364;
  --doc-muted: #748092;
  --doc-faint: #97a1af;
  --doc-line: #dfe4eb;
  --doc-soft: #eef1f5;
  --doc-accent: #4f46e5;
  min-height: calc(100vh - var(--nav-h));
  background: var(--doc-bg);
  color: var(--doc-ink);
  font-family: var(--font-sans);
}
.docs-masthead { border-bottom: 1px solid var(--doc-line); background: var(--doc-paper); }
.docs-masthead-in { max-width: 1440px; margin: 0 auto; padding: 48px 48px 40px; }
.docs-breadcrumb { display: flex; align-items: center; gap: 10px; color: var(--doc-muted); font-family: var(--font-mono); font-size: 10px; letter-spacing: .04em; }
.docs-breadcrumb i { color: var(--doc-faint); font-style: normal; }
.docs-breadcrumb strong { color: var(--doc-body); font-family: var(--font-sans); font-weight: 500; letter-spacing: 0; }
.docs-masthead h1 { margin-top: 22px; color: var(--doc-ink); font-size: clamp(34px, 4vw, 46px); font-weight: 650; line-height: 1.2; letter-spacing: -.035em; }
.docs-masthead h1 + p { max-width: 760px; margin-top: 16px; color: var(--doc-body); font-size: 15px; line-height: 1.85; }
.docs-meta { display: flex; flex-wrap: wrap; gap: 10px 22px; margin-top: 24px; color: var(--doc-muted); font-size: 11px; }
.docs-meta span { position: relative; }
.docs-meta span + span::before { content: ''; position: absolute; top: 50%; left: -12px; width: 3px; height: 3px; border-radius: 50%; background: var(--doc-faint); }
.docs-layout { display: grid; grid-template-columns: 220px minmax(0, 930px); gap: clamp(36px, 4.5vw, 66px); max-width: 1440px; margin: 0 auto; padding: 0 48px; align-items: start; }
.eng-toc { position: sticky; top: var(--nav-h); align-self: start; height: calc(100vh - var(--nav-h)); padding: 43px 22px 32px 0; overflow-y: auto; border-right: 1px solid var(--doc-line); }
.toc-desktop-title { display: grid; gap: 5px; padding-bottom: 19px; border-bottom: 1px solid var(--doc-line); }
.toc-desktop-title > span { color: var(--doc-ink); font-size: 13px; font-weight: 650; }
.toc-desktop-title small { color: var(--doc-faint); font-family: var(--font-mono); font-size: 8px; letter-spacing: .09em; }
.toc-toggle { display: none; }
.eng-toc nav { display: grid; margin-top: 14px; }
.eng-toc nav a { position: relative; display: grid; grid-template-columns: 25px 1fr; gap: 8px; padding: 11px 8px 11px 10px; border-radius: 5px; }
.eng-toc nav a::before { content: ''; position: absolute; top: 9px; bottom: 9px; left: -23px; width: 2px; background: transparent; }
.eng-toc nav a > span { padding-top: 2px; color: var(--doc-faint); font-size: 9px; }
.eng-toc nav strong { display: block; color: var(--doc-body); font-size: 12px; font-weight: 520; }
.eng-toc nav small { display: block; margin-top: 3px; color: var(--doc-faint); font-size: 8.5px; line-height: 1.45; }
.eng-toc nav a:hover { background: var(--doc-soft); }
.eng-toc nav a.active { background: #ecebfc; }
.eng-toc nav a.active::before { background: var(--doc-accent); }
.eng-toc nav a.active > span, .eng-toc nav a.active strong { color: var(--doc-accent); }
.eng-document { min-width: 0; padding: 43px 0 72px; }
.docs-intro { display: grid; grid-template-columns: 82px 1fr; gap: 16px; padding: 16px 18px; border: 1px solid #d9dff8; border-radius: 6px; background: #f1f2fd; }
.docs-intro strong { color: var(--doc-accent); font-size: 12px; font-weight: 650; }
.docs-intro p { color: #525a80; font-size: 12px; line-height: 1.8; }
.eng-section { padding: 48px 0 52px; border-bottom: 1px solid var(--doc-line); scroll-margin-top: 88px; }
.eng-chapter-head { display: block; }
.chapter-no { display: block; margin-bottom: 11px; color: var(--doc-accent); font-size: 10px; font-weight: 600; letter-spacing: .08em; }
.eng .eyebrow { color: var(--doc-muted); font-size: 9px; letter-spacing: .1em; }
.eng .eyebrow::before { display: none; }
.eng-chapter-head h2 { margin-top: 9px; color: var(--doc-ink); font-size: clamp(25px, 2.7vw, 32px); font-weight: 650; line-height: 1.4; letter-spacing: -.025em; }
.chapter-lede { max-width: 800px; margin: 18px 0 28px; color: var(--doc-body); font-size: 14.5px; line-height: 1.9; }
.eng-grid { display: block; }
.eng-copy { min-width: 0; }
.eng-flow { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px; margin: 0 0 28px; padding: 15px 17px; border: 1px solid var(--doc-line); border-radius: 6px; background: var(--doc-soft); }
.eng-flow span { color: #36445a; font-family: var(--font-mono); font-size: 10.5px; line-height: 1.7; }
.eng-flow i { color: var(--doc-accent); font-style: normal; font-size: 10px; }
.eng-copy h3 { margin-top: 27px; color: var(--doc-ink); font-size: 17px; font-weight: 650; line-height: 1.55; letter-spacing: -.01em; }
.eng-copy > p { margin-top: 9px; color: var(--doc-body); font-size: 14px; line-height: 1.95; }
.eng-copy code, .chapter-lede code { padding: 2px 5px; border: 1px solid #e0e4eb; border-radius: 4px; background: #f0f2f6; color: #3f4b62; font-family: var(--font-mono); font-size: .88em; }
.eng-detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0; margin: 26px 0 30px; border-top: 1px solid var(--doc-line); border-left: 1px solid var(--doc-line); border-radius: 6px; overflow: hidden; }
.eng-detail-grid > div { position: relative; min-width: 0; padding: 19px 20px 18px; border-right: 1px solid var(--doc-line); border-bottom: 1px solid var(--doc-line); background: var(--doc-paper); }
.detail-index { display: block; margin-bottom: 8px; color: var(--doc-accent); font-family: var(--font-mono); font-size: 9px; letter-spacing: .05em; }
.eng-detail-grid h4 { color: var(--doc-ink); font-size: 13px; font-weight: 650; line-height: 1.55; }
.eng-detail-grid p { margin-top: 7px; color: var(--doc-body); font-size: 12px; line-height: 1.85; }
.eng-detail-grid code { padding: 1px 3px; font-size: .9em; }
.eng-code { margin: 22px 0 26px; padding: 18px 20px; overflow-x: auto; border: 1px solid #dce2eb; border-radius: 6px; background: #f2f5f8; color: #3d4b61; font-family: var(--font-mono); font-size: 11px; line-height: 1.9; white-space: pre; }
.eng-code code { padding: 0; border: 0; background: transparent; color: inherit; font-size: inherit; }
.eng-run-evidence { margin-top: 36px; padding-top: 27px; border-top: 1px solid var(--doc-line); }
.eng-run-evidence > span { color: var(--doc-accent); font-size: 9px; font-weight: 600; letter-spacing: .08em; }
.eng-run-evidence h3 { margin-top: 9px; color: var(--doc-ink); font-size: 18px; font-weight: 650; line-height: 1.55; }
.eng-run-evidence h3 code { padding: 2px 5px; border: 1px solid #e0e4eb; border-radius: 4px; background: #f0f2f6; color: #3f4b62; font-family: var(--font-mono); font-size: .88em; }
.eng-run-evidence p { max-width: 850px; margin-top: 10px; color: var(--doc-body); font-size: 13px; line-height: 1.9; }
.eng-run-evidence p code { padding: 1px 4px; border: 1px solid #e0e4eb; border-radius: 4px; background: #f0f2f6; color: #3f4b62; font-family: var(--font-mono); font-size: .88em; }
.eng-screenshots { display: grid; gap: 30px; margin-top: 23px; }
.eng-table-wrap { margin: 22px 0 28px; overflow-x: auto; border: 1px solid var(--doc-line); border-radius: 6px; background: var(--doc-paper); }
.eng-spec-table { min-width: 620px; }
.eng-spec-table th, .eng-spec-table td { width: auto; padding: 12px 14px; white-space: normal; }
.eng-spec-table td:first-child { width: 105px; }
.eng-spec-table td code { white-space: nowrap; }
.eng-boundary { margin-top: 28px; padding: 15px 17px; border: 1px solid var(--doc-line); border-left: 3px solid #8b85ed; border-radius: 5px; background: var(--doc-paper); }
.eng-boundary strong { color: #554db5; font-size: 11px; font-weight: 650; }
.eng-boundary p { margin-top: 6px; color: var(--doc-body); font-size: 12px; line-height: 1.85; }
.eng-paths { display: grid; gap: 6px; margin-top: 25px; padding-top: 18px; border-top: 1px solid var(--doc-line); }
.eng-paths span { margin-bottom: 2px; color: var(--doc-muted); font-size: 10px; font-weight: 600; }
.eng-paths code { color: var(--doc-muted); font-family: var(--font-mono); font-size: 10px; line-height: 1.7; overflow-wrap: anywhere; }
.eng-grid > .engineering-media { margin-top: 30px; }
.robot-chapter .chapter-no, .robot-chapter .eyebrow { color: #087b68; }
.robot-chapter .eng-boundary { border-left-color: #42a894; }
.robot-chapter .eng-boundary strong { color: #087b68; }
table { width: 100%; border-collapse: collapse; text-align: left; }
th, td { padding: 14px 16px; border-bottom: 1px solid var(--doc-line); vertical-align: top; font-size: 12px; line-height: 1.75; }
th { background: var(--doc-soft); color: var(--doc-body); font-size: 10px; font-weight: 650; }
td { color: var(--doc-body); }
td:first-child { width: 105px; color: var(--doc-ink); font-weight: 600; white-space: nowrap; }
td:last-child { width: 105px; white-space: nowrap; }
td span { display: inline-block; padding: 2px 7px; border-radius: 4px; background: #fff4cf; color: #806014; font-size: 10px; }
tbody tr:last-child td { border-bottom: 0; }
.docs-pager { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding-top: 28px; border-top: 1px solid var(--doc-line); }
.docs-pager a { display: grid; gap: 7px; padding: 17px 18px; border: 1px solid var(--doc-line); border-radius: 6px; background: var(--doc-paper); }
.docs-pager a:last-child { text-align: right; }
.docs-pager small { color: var(--doc-muted); font-size: 10px; }
.docs-pager span { color: var(--doc-accent); font-size: 13px; font-weight: 600; }
.docs-pager a:hover { border-color: #b9b4ee; }
@media (max-width: 1100px) {
  .docs-layout { grid-template-columns: 190px minmax(0, 1fr); gap: 34px; padding: 0 32px; }
  .docs-masthead-in { padding-right: 32px; padding-left: 32px; }
  .eng-toc nav a::before { left: -23px; }
}
@media (max-width: 850px) {
  .docs-layout { display: block; padding: 0 28px; }
  .eng-toc { position: static; height: auto; padding: 20px 0 0; overflow: visible; border-right: 0; }
  .toc-desktop-title { display: none; }
  .toc-toggle { display: flex; width: 100%; align-items: center; justify-content: space-between; padding: 12px 14px; border: 1px solid var(--doc-line); border-radius: 6px; background: var(--doc-paper); color: var(--doc-body); font-size: 12px; }
  .eng-toc nav { display: none; margin: 8px 0 0; padding: 8px; border: 1px solid var(--doc-line); border-radius: 6px; background: var(--doc-paper); }
  .eng-toc nav.open { display: grid; }
  .eng-toc nav a::before { display: none; }
  .eng-document { padding-top: 20px; }
}
@media (max-width: 600px) {
  .docs-masthead-in { padding: 35px 20px 30px; }
  .docs-masthead h1 { font-size: 34px; }
  .docs-masthead h1 + p { font-size: 14px; }
  .docs-layout { padding: 0 20px; }
  .docs-intro { grid-template-columns: 1fr; gap: 6px; }
  .eng-section { padding: 40px 0 44px; }
  .eng-chapter-head h2 { font-size: 25px; }
  .chapter-lede { font-size: 14px; }
  .eng-copy > p { font-size: 13.5px; }
  .eng-flow { align-items: flex-start; flex-direction: column; gap: 4px; }
  .eng-flow i { transform: rotate(90deg); }
  .eng-detail-grid { grid-template-columns: 1fr; }
  .eng-detail-grid > div { padding: 17px 18px; }
  .eng-code { padding: 15px 16px; font-size: 10px; }
  .docs-pager { grid-template-columns: 1fr; }
  .docs-pager a:last-child { text-align: left; }
  th, td { padding: 12px; }
}
</style>
