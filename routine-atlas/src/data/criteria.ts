/* 解剖页：用一份虚构的 play_music 示例观察注册、声明、执行、停止与系统查询。
   示例只承担说明，不指向仓库中的真实文件。 */

export interface Criterion {
  key: string
  no: string
  title: string
  en: string
  line: string
  note: string
  hint?: string
  system?: boolean
}

export const CRITERIA: Criterion[] = [
  {
    key: 'name', no: '01', title: '注册', en: 'name',
    line: '让实现成为可被发现的单元。',
    note: '注册负责把实现接入系统，并确定它在清单中的名字。调用与查询通过这个名字找到单元；名字可以显式指定，也可以按规则派生。',
  },
  {
    key: 'meta', no: '02', title: '声明', en: 'meta',
    line: '描述能力、输入与使用接口。',
    note: '声明负责告诉外部这个单元能做什么、需要哪些输入，以及使用时应当了解的信息。它面向发现与使用，不承担具体执行。',
  },
  {
    key: 'run', no: '03', title: '执行', en: 'run',
    line: '承载一次调用的实际工作。',
    note: '执行负责接收输入、推进任务并产出结果。每次调用都有独立的命令实例；执行完成，本次实例的生命周期也随之完成。',
  },
  {
    key: 'stop', no: '04', title: '停止', en: 'stop',
    line: '中断执行并收尾实例资源。',
    note: '停止负责中断仍在进行的工作，并清理本次实例占用的资源。它属于生命周期管理，不是另一项业务调用；没有收尾工作需要处理时可以不提供。',
  },
  {
    key: 'discover', no: '05', title: '查询', en: 'routine/list · routine/doc',
    line: '让外部发现单元并观察状态。',
    note: '查询负责读取注册信息、能力声明与运行状态，让外部能够发现和观察单元。它由系统统一提供，不要求每个业务单元重复实现。',
    hint: '系统提供',
    system: true
  }
]

/* 示例代码：不是仓库里的真实文件，只用来把五个观察面讲清楚。 */
export const SPECIMEN = {
  name: 'play_music',
  cls: 'PlayMusic',
  tag: '示例代码 · 非仓库实现',
  file: 'play_music.py',
  code: [
    'class PlayMusic(Routine):',
    '    """播放一首曲子，可随时停止。"""',
    '',
    "    name = 'play_music'",
    "    meta = {'description': '播放一首曲子，可随时停止'}",
    '',
    '    async def run(self, kwargs: Dict[str, Any]):',
    "        title = kwargs.get('title', 'river')",
    "        await self.audio.play(title)",
    "        await asyncio.sleep(kwargs.get('seconds', 30))",
    "        return {'content': f'播放完成: {title}'}",
    '',
    '    async def stop(self) -> None:',
    '        await self.audio.stop()'
  ],
  marks: {
    name: [4, 4],
    meta: [5, 5],
    run: [7, 11],
    stop: [13, 14]
  } as Record<string, [number, number]>
}
