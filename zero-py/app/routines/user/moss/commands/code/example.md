**一格** = 一段 async Python, 直接写; 收完才执行.

    await call('sleep', duration=0.1)    # 建子实例并等它
    print('做完了')

**参数与 body** = 属性是参数, 中间那段是内容. 同一个调用的两个前端:

    <sleep duration="1"/>             ⇔   call('sleep', duration=1)
    <code timeout="5">print(1)</code> ⇔   call('code', body='print(1)', timeout=5)
    <say>hello</say>                  ⇔   call('say', body='hello')

参数按名字传, 名字以清单里的签名为准(写错名字当场抛 `CallError`, 不会
悄悄忽略); body 只给收 body 的 routine(它那行说明写着 "body = …").
code 自己的 body 就是上面那段源码 —— 给子 routine 喂 body 按"要不要边产边送 /
要不要在中间插手"选一档:

    await call('say', body='一步到')          # 整段给完再等结果
    await call('say', body=lines())          # 异步生成器: 每 yield 一段就送一段
                                             # (lines() 是 async def 里 yield 的)
    h = await create('say'); await h.start() # 要分段自己控:
    await h.feed('第一段'); await h.eof()     #   递一段 → eof(或 stop 收口)
    print(await h)                           #   回执

**等外界** 一格独占内核, 格内 `await` 就是"这条 code 还在跑"(有上限).
等得久 / 要等的东西不归这一格管 → `spawn` 起一条后台任务, 格立刻收尾:

    t = spawn(watch_config(), name='watch-config', desc='等 config.json 改动')
    # 下一格
    t.cancel()                                       # 停不停由你决定

`name` 对号, `desc` 说"在等什么" —— 回执两处都报(`运行中 1 条:
watch-config(等 config.json 改动)`; 抛错则 `失败 1 条: bad(等构建) →
ValueError: boom`), 因为下一拍的你只看得到那一行. 抛错也在下一格回执里报,
不当 stderr 里一句 warning 了事. 它里面的 `call` 挂**内核**名下(不入任何一格):
这条任务的意义就是活过发起它的那一格, 所以停 code 不会连带停它 —— 要停只有
`t.cancel()`. 取消(或它自己抛错收场)会**连带收掉它 `call`/`create` 出来的
实例**(say 截声、别的实例走完自己的停路径): 任务都不要了, 它启动的事不该留着跑.
**叫不动** 容器(并行用 asyncio.gather)、两格并行(一次一格, 第二格当场被拒).
**注意** 源码里的 `<` 写成 `&lt;` 更稳(收回来就是 `<`): `a<b` 会被当成标签
开头, 一直等 `>`, 中间的字跟着遭殃; `&lt;` 绝无歧义. 另: 别写出闭合标签的
字样, 这段 body 会提前结束.
