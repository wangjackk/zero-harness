import type { Action } from "@bb/shared";

export type Batch = { act: Action; id: string }[];

/**
 * 假动作流：模拟"模型已经讲完这段"，按批吐出来。
 * 接真模型之前，所有动画参数都靠它调。
 *
 * id 就是它产生的对象 id —— 后面的 ring 靠 id 引用前面的 write。
 *
 * 注意直角那条腿：底边 + 竖边用**一条 poly** 画完，不是两条 line。
 * 两条 line 是两笔，各自在角上收细，接缝看得出来。
 */
export const DEMO: Batch[] = [
  [{ id: "t1", act: { do: "write", text: "斜率就是变化率", at: [0.07, 0.20], size: "l" } }],

  [{ id: "l1", act: { do: "line", from: [0.15, 0.80], to: [0.70, 0.42] } }],

  [
    {
      id: "leg",
      act: {
        do: "poly",
        points: [
          [0.15, 0.80],
          [0.70, 0.80],
          [0.70, 0.42],
        ],
      },
    },
  ],

  [
    { id: "p1", act: { do: "point", at: [0.15, 0.80] } },
    { id: "p2", act: { do: "point", at: [0.70, 0.42] } },
  ],

  [
    { id: "la", act: { do: "write", text: "A", at: [0.118, 0.878], size: "m" } },
    { id: "lb", act: { do: "write", text: "B", at: [0.726, 0.400], size: "m" } },
  ],

  [
    { id: "lx", act: { do: "write", text: "Δx", at: [0.395, 0.885], size: "m" } },
    { id: "ly", act: { do: "write", text: "Δy", at: [0.628, 0.628], size: "m" } },
  ],

  [
    { id: "c1", act: { do: "ring", around: "t1", pad: 14 } },
    { id: "f1", act: { do: "write", text: "Δy / Δx", at: [0.715, 0.215], size: "l" } },
  ],

  // 标准形状：矩形、椭圆、正圆，各是一整条笔迹。at 是中心，尺寸单位都是画布短边
  [
    { id: "sq", act: { do: "rect", at: [0.87, 0.70], w: 0.14, h: 0.14 } },
    { id: "el", act: { do: "ellipse", at: [0.865, 0.46], rx: 0.055, ry: 0.075 } },
    { id: "cc", act: { do: "circle", at: "above:el", r: 0.055 } },
  ],
];
