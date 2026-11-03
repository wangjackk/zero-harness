/* 自举页：8 个拍点。日志格式取自 routines/loader.py 的 _log 调用，用作示意。
   这一页只跟一条线：一份清单（routines.yaml）怎么把自己交给 kernel，然后系统怎么开始改自己。 */

export interface LogLine { kind: 'sys' | 'ok' | 'dim' | 'warn'; text: string }

export interface Beat {
  id: string
  no: string
  title: string
  claim: string
  detail: string
  logs: LogLine[]
  metric?: { k: string; v: string }[]
}

export const BEATS: Beat[] = [
  {
    id: 'empty', no: '00', title: '空表', claim: '注册表为空。',
    detail: 'kernel 仅维护 name → 提供方的路由表、命令树和投递通道，不解析 agent 或车辆等领域语义。hub 位于 kernel 外部，承担注册入口而非内核职责。',
    logs: [
      { kind: 'sys', text: 'kernel: 实例表就绪 · 投递通道已开' },
      { kind: 'dim', text: 'catalog: 0 routines registered' }
    ],
    metric: [{ k: '注册名', v: '0' }]
  },
  {
    id: 'seed', no: '01', title: '初始注册', claim: '唯一静态注册的单元。',
    detail: 'kernel 的启动参数仅硬编码 routines_loader 的注册名。该单元设置 is_passive=True，由 kernel 自动启动；其自身不列入清单，元层不进入清单。',
    logs: [
      { kind: 'sys', text: 'kernel: passive auto-start → routines_loader' },
      { kind: 'ok', text: 'routines_loader: yaml_path() = app/routines.yaml' }
    ],
    metric: [{ k: '硬编码', v: '1 个注册名' }]
  },
  {
    id: 'check', no: '02', title: '预检', claim: '无效清单必须阻止启动。',
    detail: 'check_yaml 在 kernel 启动前验证文件存在、格式可解析、条目非空及路径有效；任一条件不满足即退出。进程正常运行但未注册任何单元，比直接启动失败更难诊断。',
    logs: [
      { kind: 'sys', text: 'loader: check_yaml(app/routines.yaml)' },
      { kind: 'ok', text: 'loader: all entries ok' }
    ],
    metric: [{ k: '失败退出', v: 'exit 2' }]
  },
  {
    id: 'register', no: '03', title: '注册', claim: '引导单元仅提交注册项。',
    detail: '逐个 import，读取 __all__ 中的 Routine 子类，并通过 hub.register_routine(cls) 等待 kernel 回执。重连时的同名冲突予以跳过，不作为错误。当前清单包含 82 个注册名。',
    logs: [
      { kind: 'dim', text: 'loader: import app.routines.user.car → 14 classes' },
      { kind: 'ok', text: '✅ car/move' },
      { kind: 'ok', text: '✅ car/status' },
      { kind: 'ok', text: '✅ car/snap' },
      { kind: 'ok', text: '✅ mindflow' },
      { kind: 'dim', text: '… 其余 78 个' },
      { kind: 'sys', text: 'loader: routines.yaml → registered 82, skipped 0' }
    ],
    metric: [{ k: '注册名', v: '82' }, { k: '耗时', v: '一次性' }]
  },
  {
    id: 'handoff', no: '04', title: '引导完成', claim: '引导单元完成后自动停止。',
    detail: 'run() 正常返回后，命令实例自动停止；kernel 通过 passiveStarted 去重，不再启动该实例。后续文件变更由 watcher 处理，因此引导单元无需保持运行。',
    logs: [
      { kind: 'sys', text: 'routines_loader: run() returned' },
      { kind: 'dim', text: 'kernel: instance auto-stopped (passive dedupe)' },
      { kind: 'ok', text: 'routines_watcher: watching routines.yaml' }
    ],
    metric: [{ k: '常驻引导器', v: '0' }]
  },
  {
    id: 'reload', no: '05', title: '热重载', claim: '热重载保持注册名稳定。',
    detail: 'watcher 监控 mtime，并仅处理发生变化的条目：停止旧实例、注册新类且保持注册名不变，因此调用路由无需修改，其他单元和进程状态也不受影响。',
    logs: [
      { kind: 'dim', text: 'watcher: mtime changed → car/status.py' },
      { kind: 'sys', text: 'reload car/status …' },
      { kind: 'ok', text: 'reload car/status → ok (0.31s), route kept' }
    ],
    metric: [{ k: '进程重启', v: '0 次' }]
  },
  {
    id: 'grow', no: '06', title: '生长', claim: '新实现成为可调用的能力。',
    detail: '创建或修改可以来自人，也可以来自 Agent。候选实现经过必要检查并加载后，便能通过注册名进入后续调用；具体创作与检查方式由应用决定。',
    logs: [
      { kind: 'sys', text: 'candidate: routine implementation ready' },
      { kind: 'dim', text: 'load: update routine implementation …' },
      { kind: 'ok', text: 'registry: capability ready for invocation' }
    ],
    metric: [{ k: '能力来源', v: '人或 Agent' }]
  },
  {
    id: 'axiom', no: '07', title: '结论', claim: '启动过程同样由注册单元发起。',
    detail: '引导、热重载、自修改、能力清单、提示词和设备操作均使用统一注册结构；系统的管理功能也由系统内的单元实现。',
    logs: [
      { kind: 'sys', text: 'catalog: 82 routines · 1 table · 1 dispatch' },
      { kind: 'ok', text: 'axiom: 系统自身也在它自己的注册表里' }
    ],
    metric: [{ k: '静态代码', v: '1 个类' }]
  }
]

export const BOOT_AXIOM = '自举让系统用自己的单元启动和修改自己；当创作由 Agent 驱动，同一个闭环也能让新能力进入下一次任务。'
