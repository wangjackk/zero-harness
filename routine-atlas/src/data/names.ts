/* 表里的名字：一张表，三种实现。
   - py 那 123 个是探针从真实 loader 导出的（registry.json）。
   - ts 的 9 个、rust 的 6 个是手抄的，但每一个都能在代码里指出注册名
     （ts: `static name = '...'`；rs: `const NAME: &'static str = "..."`）。
   kernel 里本来就只有一张表 —— 按实现拆开是页面的读法，不是系统的事实。
   所以这里合成 NAMES，谁要“全部名字”就用它，不要再用 registry.length。 */

import registry from './registry.json'
import type { Impl } from './bands'

export interface NameRow {
  name: string
  cls: string
  file: string
  desc: string
  passive?: boolean
  input?: boolean
  inZero?: boolean
  inMoss?: boolean
  impl?: Impl
}

/* zero-ts/app/routines/user/agents/pi/ —— 拨入 py 侧 kernel 的增量包。
   共享 kernel 里名字全局唯一，所以这里只带 py 侧没有的 pi 引擎。 */
const TS: NameRow[] = [
  {
    name: 'pi/agent', cls: 'PiAgent', impl: 'ts',
    file: 'zero-ts/app/routines/user/agents/pi/agent.ts',
    desc: 'pi 引擎：唯一工具为 ipython，该调用经 kernel 路由到 py 侧同名单元。',
    passive: false, input: true
  },
  {
    name: 'pi/agent/manager', cls: 'PiAgentManager', impl: 'ts',
    file: 'zero-ts/app/routines/user/agents/pi/manager.ts',
    desc: '常驻 manager：启动时向 agent_hub 注册 manifest，之后负责 create / stop / resume / list / delete。',
    passive: true, input: false
  },
  {
    name: 'pi/agent/create', cls: 'CreatePiAgent', impl: 'ts',
    file: 'zero-ts/app/routines/user/agents/pi/manager.ts',
    desc: '创建 pi Agent 命令实例，返回其 routine id。', passive: false, input: true
  },
  {
    name: 'pi/agent/stop', cls: 'StopPiAgent', impl: 'ts',
    file: 'zero-ts/app/routines/user/agents/pi/manager.ts',
    desc: '停止命令实例并保留记录，可通过 resume 恢复。', passive: false, input: true
  },
  {
    name: 'pi/agent/resume', cls: 'ResumePiAgent', impl: 'ts',
    file: 'zero-ts/app/routines/user/agents/pi/manager.ts',
    desc: '根据记录恢复已停止的命令实例。', passive: false, input: true
  },
  {
    name: 'pi/agent/list', cls: 'ListPiAgents', impl: 'ts',
    file: 'zero-ts/app/routines/user/agents/pi/manager.ts',
    desc: '列出 pi Agent 命令实例。', passive: false, input: true
  },
  {
    name: 'pi/agent/delete', cls: 'DeletePiAgents', impl: 'ts',
    file: 'zero-ts/app/routines/user/agents/pi/manager.ts',
    desc: '删除命令实例及其记录；与 stop 不同，删除后不能通过 resume 恢复。', passive: false, input: true
  },
  {
    name: 'pi/settings/get', cls: 'PiGetSettings', impl: 'ts',
    file: 'zero-ts/app/routines/user/agents/pi/settings.ts',
    desc: '读 pi 引擎的模型与参数。', passive: false, input: true
  },
  {
    name: 'pi/settings/set', cls: 'PiSetSettings', impl: 'ts',
    file: 'zero-ts/app/routines/user/agents/pi/settings.ts',
    desc: '通过单元调用修改模型和参数，无需修改配置后重启。', passive: false, input: true
  }
]

/* zero-rs/src/routines/ —— 实时音频放在 rust 侧，因为它要的不是“能跑”，是“不抖”。 */
const RS: NameRow[] = [
  {
    name: 'speak', cls: 'Speak', impl: 'rs',
    file: 'zero-rs/src/routines/speak/mod.rs',
    desc: '语音合成单元：将文本转换为音频；py 侧 say 在需要语音输出时调用该注册名。',
    passive: false, input: true
  },
  {
    name: 'audio/output/list', cls: 'AudioOutputList', impl: 'rs',
    file: 'zero-rs/src/routines/audio/list.rs',
    desc: '枚举可用的音频输出设备。', passive: false, input: true
  },
  {
    name: 'audio/output/set', cls: 'AudioOutputSet', impl: 'rs',
    file: 'zero-rs/src/routines/audio/set.rs',
    desc: '选择音频输出通道。', passive: false, input: true
  },
  {
    name: 'music/list', cls: 'ListMusic', impl: 'rs',
    file: 'zero-rs/src/routines/music/list_music.rs',
    desc: '列出曲库条目。', passive: false, input: true
  },
  {
    name: 'music/play', cls: 'PlayMusic', impl: 'rs',
    file: 'zero-rs/src/routines/music/play_music.rs',
    desc: '播放指定音乐；曲库目录由 zero-rs/config.toml 配置，并与 py 共用 assets。',
    passive: false, input: true
  },
  {
    name: 'sound_effect', cls: 'SoundEffect', impl: 'rs',
    file: 'zero-rs/src/routines/music/sound_effect.rs',
    desc: '播放短音效，不占用曲库条目。', passive: false, input: true
  }
]

export const NAMES: NameRow[] = [
  ...(registry as NameRow[]),
  ...TS,
  ...RS
]

export const NAMES_PY = (registry as NameRow[]).length
export const NAMES_TS = TS.length
export const NAMES_RS = RS.length
export const TOTAL_NAMES = NAMES.length
