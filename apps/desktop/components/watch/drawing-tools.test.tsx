import { beforeEach, describe, expect, it } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

import { DrawingTools } from "./drawing-tools";
import { ObjectTreeDock, type IndicatorObject } from "./object-tree-dock";
import { useDrawingsStore } from "@/lib/store/drawings";

const store = () => useDrawingsStore.getState();

const INPUT = {
  start: { time: 1000, price: 100 },
  end: { time: 2000, price: 200 },
  symbol: "BTC",
  timeframe: "1h",
};

const INDICATORS: IndicatorObject[] = [
  { key: "ma", label: "MA", on: true, group: "overlay" },
  { key: "boll", label: "BOLL", on: false, group: "overlay" },
];

beforeEach(() => {
  store().resetForTest();
  localStorage.clear();
});

/** 打开对象树,返回侧栏。
 *  第 4 步起树是 `.chart-area` 的常驻侧栏列,由 kf-page 与工具栏并排挂载;
 *  这里照同样的组合渲染两者,点「对象树」图标把 store 的 dockOpen 打开。 */
function open() {
  render(
    <>
      <DrawingTools symbol="BTC" />
      <ObjectTreeDock symbol="BTC" indicators={INDICATORS} />
    </>,
  );
  const btn = screen.getByRole("button", { name: /对象树/ });
  fireEvent.click(btn);
  return screen.getByRole("complementary", { name: "对象树" });
}

/** 树里的绘图行(不含指标行)。 */
function drawingRows(dialog: HTMLElement): HTMLElement[] {
  return Array.from(dialog.querySelectorAll(".kf-draw-row")).filter(
    (r) => !r.closest('[data-testid="kf-draw-indicators"]'),
  ) as HTMLElement[];
}

function batchBar(dialog: HTMLElement): HTMLElement {
  return dialog.querySelector('[data-testid="kf-draw-batch"]') as HTMLElement;
}

function batchCount(dialog: HTMLElement): string | null {
  const el = batchBar(dialog).querySelector('[data-testid="kf-draw-batch-count"]');
  return el === null ? null : el.textContent;
}

function batchButton(dialog: HTMLElement, text: string): HTMLButtonElement | undefined {
  return Array.from(batchBar(dialog).querySelectorAll(".kf-draw-batch button")).find(
    (b) => b.textContent === text,
  ) as HTMLButtonElement | undefined;
}

describe("对象树多选(第 3 步)", () => {
  it("无选中时批量按钮不渲染,只留操作提示", () => {
    store().createFromDrag(INPUT);
    store().createFromDrag(INPUT);
    store().clearSelection();
    const dialog = open();

    expect(batchBar(dialog).textContent).toContain("⌘/Ctrl 点选多项 · Shift 选区间");
    expect(batchCount(dialog)).toBeNull();
    expect(batchBar(dialog).querySelectorAll(".kf-draw-btn")).toHaveLength(0);
  });

  it("⌘/Ctrl 点选两行 → 计数为 2,批量删除把两条一起删掉", () => {
    store().createFromDrag(INPUT);
    store().createFromDrag(INPUT);
    store().clearSelection();
    const dialog = open();

    const rows = drawingRows(dialog);
    expect(rows).toHaveLength(2);

    fireEvent.click(rows[0]);
    expect(batchCount(dialog)).toBe("已选 1");
    fireEvent.click(rows[1], { metaKey: true });
    expect(batchCount(dialog)).toBe("已选 2");

    const del = batchButton(dialog, "删除")!;
    expect(del.disabled).toBe(false);
    fireEvent.click(del);
    expect(store().grids).toHaveLength(0);
    expect(store().selectedIds.size).toBe(0);
  });

  it("锁定后对象移出选中集,批量栏回到无选中态", () => {
    store().createFromDrag(INPUT);
    store().createFromDrag(INPUT);
    store().clearSelection();
    const dialog = open();

    const rows = drawingRows(dialog);
    fireEvent.click(rows[0]);
    fireEvent.click(rows[1], { metaKey: true });
    fireEvent.click(batchButton(dialog, "锁定")!);

    expect(store().grids.map((g) => g.locked)).toEqual([true, true]);
    expect(store().selectedIds.size).toBe(0);
    expect(batchCount(dialog)).toBeNull();
  });

  it("批量隐藏是绝对值:混选(一显一隐)点「设为隐藏」后全部隐藏", () => {
    store().createFromDrag(INPUT);
    store().createFromDrag(INPUT);
    store().clearSelection();
    const dialog = open();

    // 用行内眼睛先把第一条隐藏,再全选
    const rows = drawingRows(dialog);
    fireEvent.click(rows[0].querySelector(".kf-draw-ic") as HTMLElement);
    expect(store().grids[0].visible).toBe(false);

    fireEvent.click(rows[0]);
    fireEvent.click(rows[1], { metaKey: true });
    fireEvent.click(batchButton(dialog, "设为隐藏")!);
    expect(store().grids.map((g) => g.visible)).toEqual([false, false]);
  });

  it("指标行不参与多选:点它只展开/不选中,选中集仍为空", () => {
    store().createFromDrag(INPUT);
    store().clearSelection();
    const dialog = open();

    const section = dialog.querySelector('[data-testid="kf-draw-indicators"]') as HTMLElement;
    const maRow = Array.from(section.querySelectorAll(".kf-draw-row")).find((r) =>
      r.textContent?.includes("MA"),
    ) as HTMLElement;
    fireEvent.click(maRow);

    expect(store().selectedIds.size).toBe(0);
    expect(batchCount(dialog)).toBeNull();
    // 没有变成选中态的行
    expect(dialog.querySelector(".kf-draw-row.sel")).toBeNull();
  });

  it("行内眼睛按钮不触发行选中", () => {
    store().createFromDrag(INPUT);
    store().clearSelection();
    const dialog = open();

    const row = drawingRows(dialog)[0];
    fireEvent.click(row.querySelector(".kf-draw-ic") as HTMLElement);
    expect(store().grids[0].visible).toBe(false);
    expect(store().selectedIds.size).toBe(0);
  });

  it("全选 / 清空选择 按钮互斥", () => {
    store().createFromDrag(INPUT);
    store().createFromDrag(INPUT);
    store().clearSelection();
    const dialog = open();

    const toggle = () => batchBar(dialog).querySelector(".kf-draw-batch-all") as HTMLButtonElement;

    expect(toggle().textContent).toBe("全选");
    fireEvent.click(toggle());
    expect(store().selectedIds.size).toBe(2);
    expect(toggle().textContent).toBe("清空选择");

    fireEvent.click(toggle());
    expect(store().selectedIds.size).toBe(0);
    expect(toggle().textContent).toBe("全选");
  });

  it("Shift 点选按树的可见顺序正向/反向扩选", () => {
    store().createFromDrag(INPUT);
    store().createFromDrag(INPUT);
    store().createFromDrag(INPUT);
    store().clearSelection();
    const dialog = open();

    const rows = drawingRows(dialog);
    expect(rows).toHaveLength(3);

    fireEvent.click(rows[0]);
    fireEvent.click(rows[2], { shiftKey: true });
    expect(store().selectedIds.size).toBe(3);

    fireEvent.click(rows[2]);
    expect(store().selectedIds.size).toBe(1);
    fireEvent.click(rows[0], { shiftKey: true });
    expect(store().selectedIds.size).toBe(3);
  });

  it("隐藏的对象仍在树里,且能被批量选中再显示回来", () => {
    store().createFromDrag(INPUT);
    store().setVisibleOf([store().grids[0].id], false);
    store().clearSelection();
    const dialog = open();

    const rows = drawingRows(dialog);
    expect(rows).toHaveLength(1);
    expect(rows[0].className).toContain("dim");

    fireEvent.click(rows[0]);
    fireEvent.click(batchButton(dialog, "设为可见")!);
    expect(store().grids[0].visible).toBe(true);
    expect(store().selectedIds.size).toBe(0);
  });
});


describe("绘图工具图标栏(1022 改版)", () => {
  /** 工具栏按可访问名取按钮 —— 图标本身不可读,名字全在 aria-label 上。 */
  function tool(name: string | RegExp): HTMLButtonElement {
    return screen.getByRole("button", { name }) as HTMLButtonElement
  }

  /** 七个工具:选择 + 2 个斐波那契 + 4 个基础图形 —— 一次铺开,没有下拉。 */
  const TOOL_NAMES = ["选择", "斐波那契", "时间斐波那契", "趋势线", "水平线", "竖线", "矩形"]

  function renderTools() {
    render(<DrawingTools symbol="BTC" />)
  }

  it("七个工具直接铺在工具栏上,不再有下拉触发按钮", () => {
    renderTools()
    const group = screen.getByRole("group", { name: "绘图工具" })
    expect(Array.from(group.querySelectorAll("button")).map((b) => b.getAttribute("aria-label"))).toEqual(
      TOOL_NAMES,
    )
    // 旧形态的遗物:menu 语义的触发按钮与弹层都必须消失
    expect(document.querySelector('button[aria-haspopup="menu"]')).toBeNull()
    expect(screen.queryByRole("menu")).toBeNull()
  })

  it("每个按钮都是 22×22 的图标位,内含 svg", () => {
    renderTools()
    for (const name of TOOL_NAMES) {
      const btn = tool(name)
      expect(btn.className).toContain("chart-tool-icon")
      // 按钮内只有图形,没有可见文字节点(文案在 aria-label / title)
      expect(btn.querySelector("svg")).not.toBeNull()
      expect(btn.textContent?.trim()).toBe("")
    }
  })

  it("点图标即选中工具,aria-pressed 跟着走;选择按钮在非绘图态为按下", () => {
    renderTools()
    expect(tool("选择").getAttribute("aria-pressed")).toBe("true")

    fireEvent.click(tool("趋势线"))
    expect(store().activeTool).toBe("trend")
    expect(tool("趋势线").getAttribute("aria-pressed")).toBe("true")
    expect(tool("选择").getAttribute("aria-pressed")).toBe("false")
    expect(tool("趋势线").className).toContain("active")
  })

  it("再点一次已激活的工具 = 退出绘图态(与 TV 同语义)", () => {
    renderTools()
    fireEvent.click(tool("矩形"))
    expect(store().activeTool).toBe("rect")
    fireEvent.click(tool("矩形"))
    expect(store().activeTool).toBe("select")
  })

  it("任意时刻至多一个工具处于按下态", () => {
    store().setActiveTool("horizontal")
    renderTools()
    const pressed = screen
      .getByRole("group", { name: "绘图工具" })
      .querySelectorAll('[aria-pressed="true"]')
    expect(Array.from(pressed).map((b) => b.getAttribute("aria-label"))).toEqual(["水平线"])
  })

  it("点「选择」回到不绘图的默认态", () => {
    store().setActiveTool("vertical")
    renderTools()
    fireEvent.click(tool("选择"))
    expect(store().activeTool).toBe("select")
  })

  it("无选中时不渲染删除按钮;有选中才出现,点它删掉整份选中集", () => {
    store().createFromDrag(INPUT)
    store().createFromDrag(INPUT)
    store().clearSelection()
    renderTools()
    expect(screen.queryByRole("button", { name: /删除选中/ })).toBeNull()

    const ids = store().grids.map((g) => g.id)
    act(() => {
      store().select(ids[0])
      store().toggleSelect(ids[1])
    })
    const del = screen.getByRole("button", { name: "删除选中的 2 项" })
    expect(del.querySelector(".chart-tool-badge")?.textContent).toBe("2")

    fireEvent.click(del)
    expect(store().grids).toHaveLength(0)
    // 删完自己消失
    expect(screen.queryByRole("button", { name: /删除选中/ })).toBeNull()
  })

  it("删除按钮的 aria-label 随选中数变化", () => {
    const a = store().createFromDrag(INPUT)
    store().createFromDrag(INPUT)
    act(() => store().select(a))
    renderTools()
    expect(screen.getByRole("button", { name: "删除选中的 1 项" })).toBeTruthy()
  })

  it("对象树按钮:计数只算当前标的,aria-controls 指向侧栏", () => {
    store().createFromDrag(INPUT)
    store().createFromDrag({ ...INPUT, symbol: "ETH" })
    renderTools()
    const btn = tool(/对象树/)
    expect(btn.querySelector(".chart-tool-badge")?.textContent).toBe("1")
    expect(btn.getAttribute("aria-controls")).toBe("kf-object-tree")
    expect(btn.getAttribute("aria-pressed")).toBe("false")

    fireEvent.click(btn)
    expect(store().dockOpen).toBe(true)
    expect(btn.getAttribute("aria-pressed")).toBe("true")
  })
})


describe("对象树侧栏 + 搜索 + 组级开关(第 4 步)", () => {
  /** 造一条带名字的网格;`createFromDrag` 不接 label,名字走 rename。 */
  function grid(label: string, symbol = "BTC"): string {
    const id = store().createFromDrag({ ...INPUT, symbol });
    store().rename(id, label);
    return id;
  }
  /** 新建分组并返回它的 id。`addGroupNamed` 不返回 id,建完是 groups 末位。 */
  function group(name: string): string {
    store().addGroupNamed(name);
    return store().groups[store().groups.length - 1].id;
  }

  it("收起时不渲染侧栏,点「对象树」图标展开、点折叠按钮收起", () => {
    grid("a");
    render(
      <>
        <DrawingTools symbol="BTC" />
        <ObjectTreeDock symbol="BTC" indicators={INDICATORS} />
      </>,
    );
    expect(screen.queryByRole("complementary", { name: "对象树" })).toBeNull();

    const btn = screen.getByRole("button", { name: /对象树/ });
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(btn);
    expect(btn.getAttribute("aria-pressed")).toBe("true");
    const dock = screen.getByRole("complementary", { name: "对象树" });

    fireEvent.click(dock.querySelector(".kf-dock-collapse") as HTMLElement);
    expect(screen.queryByRole("complementary", { name: "对象树" })).toBeNull();
    expect(btn.getAttribute("aria-pressed")).toBe("false");
  });

  it("搜索按名称过滤;清空后全部回来", () => {
    grid("alpha");
    grid("beta");
    grid("gamma");
    const dock = open();

    const input = dock.querySelector('[data-testid="kf-dock-search"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: "be" } });
    expect(drawingRows(dock)).toHaveLength(1);
    expect(drawingRows(dock)[0].textContent).toContain("beta");

    fireEvent.change(input, { target: { value: "" } });
    expect(drawingRows(dock)).toHaveLength(3);
  });

  it("搜索也命中标的,且大小写不敏感", () => {
    grid("one", "BTC");
    grid("two", "ETH");
    const dock = open();
    // 切到「全部标的」后两个标的都在树里
    fireEvent.click(dock.querySelector(".kf-draw-scope") as HTMLElement);
    expect(drawingRows(dock)).toHaveLength(2);

    const input = dock.querySelector('[data-testid="kf-dock-search"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: "eTh" } });
    expect(drawingRows(dock)).toHaveLength(1);
    expect(drawingRows(dock)[0].textContent).toContain("two");
  });

  it("搜索无命中 → 空态文案说「没有匹配」,而不是「当前图上还没有对象」", () => {
    grid("alpha");
    const dock = open();
    const input = dock.querySelector('[data-testid="kf-dock-search"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: "zzz" } });

    expect(drawingRows(dock)).toHaveLength(0);
    expect(dock.textContent).toContain("没有匹配「zzz」的对象。");
    expect(dock.textContent).not.toContain("当前图上还没有对象。");
  });

  it("搜索只过滤显示,不清空选中:被筛掉的行仍会被批量按钮作用到", () => {
    grid("alpha");
    grid("beta");
    const dock = open();

    const rows = drawingRows(dock);
    fireEvent.click(rows[0]);
    fireEvent.click(rows[1], { metaKey: true });
    expect(store().selectedIds.size).toBe(2);

    const input = dock.querySelector('[data-testid="kf-dock-search"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: "alpha" } });
    expect(drawingRows(dock)).toHaveLength(1);
    expect(batchCount(dock)).toBe("已选 2");

    fireEvent.click(batchButton(dock, "设为隐藏")!);
    expect(store().grids.map((g) => g.visible)).toEqual([false, false]);
  });

  it("组级眼睛:全显 → ◉;混一条隐藏 → ◐;点一下统一到开", () => {
    const gid = group("组 A");
    const a = grid("a");
    const b = grid("b");
    store().assignGroup(a, gid);
    store().assignGroup(b, gid);
    const dock = open();

    const eye = () => dock.querySelector('[data-testid="kf-group-vis"]') as HTMLElement;
    expect(eye().textContent).toBe("◉");

    // 用行内眼睛藏掉一条(store 直调不会触发 React 重渲染,得走 UI)
    fireEvent.click(drawingRows(dock)[0].querySelector(".kf-draw-ic") as HTMLElement);
    expect(eye().textContent).toBe("◐");

    // 混合态点一下 = 统一到"开"(nextGroupFlag),不是逐条翻转
    fireEvent.click(eye());
    expect(store().grids.every((g) => g.visible)).toBe(true);

    // 全开再点 = 全关
    fireEvent.click(eye());
    expect(store().grids.every((g) => !g.visible)).toBe(true);
  });

  it("组级锁:全未锁 → 点击全部锁定,再点全部解锁", () => {
    const gid = group("组 B");
    const a = grid("a");
    const b = grid("b");
    store().assignGroup(a, gid);
    store().assignGroup(b, gid);
    const dock = open();

    const lock = () => dock.querySelector('[data-testid="kf-group-lock"]') as HTMLElement;
    expect(lock().textContent).toBe("▢");
    fireEvent.click(lock());
    expect(store().grids.every((g) => g.locked)).toBe(true);
    expect(lock().textContent).toBe("▣");
    fireEvent.click(lock());
    expect(store().grids.every((g) => !g.locked)).toBe(true);
  });

  it("组级开关只作用于该组,不波及顶层行", () => {
    const gid = group("组 C");
    const inGroup = grid("in");
    const topLevel = grid("top");
    store().assignGroup(inGroup, gid);
    const dock = open();

    fireEvent.click(dock.querySelector('[data-testid="kf-group-vis"]') as HTMLElement);
    const byId = (id: string) => store().grids.find((g) => g.id === id)!;
    expect(byId(inGroup).visible).toBe(false);
    expect(byId(topLevel).visible).toBe(true);
  });

  it("折叠分组后其中的行不渲染,全选也不含它们", () => {
    const gid = group("组 D");
    const a = grid("a");
    store().assignGroup(a, gid);
    grid("b");
    grid("c");
    const dock = open();

    fireEvent.click(dock.querySelector(".kf-draw-group-head .kf-draw-ic") as HTMLElement);
    // 折叠后可见行 = 顶层的两条
    expect(drawingRows(dock)).toHaveLength(2);
    // `createFromDrag` 画完会自动选中,c 还在选中集里 —— 先清空,
    // 否则「全选」按钮此刻是「清空选择」。(store 直调须包 act,否则 DOM 不重渲染)
    act(() => store().clearSelection());
    fireEvent.click(batchBar(dock).querySelector(".kf-draw-batch-all") as HTMLElement);
    expect(store().selectedIds.size).toBe(2);
    expect(store().selectedIds.has(a)).toBe(false);
  });
});

describe("对象树 · 自定义档位增删", () => {
  /** 画一条斐波那契网格并展开它的档位面板。 */
  function openExpanded(): { dock: HTMLElement; id: string } {
    const id = store().createFromDrag(INPUT);
    const dock = open();
    // 指标行也有 .kf-draw-caret,必须限定在绘图行里点。
    fireEvent.click(drawingRows(dock)[0].querySelector(".kf-draw-caret") as HTMLElement);
    return { dock, id };
  }

  const addRow = (dock: HTMLElement) => dock.querySelector(".kf-draw-level-add") as HTMLElement;
  const ratioInput = (dock: HTMLElement) => addRow(dock).querySelector("input") as HTMLInputElement;
  const addBtn = (dock: HTMLElement) =>
    addRow(dock).querySelector('button[aria-label="添加档位"]') as HTMLElement;
  const errText = (dock: HTMLElement) =>
    (dock.querySelector(".kf-draw-level-err") as HTMLElement).textContent;
  const levelsOf = (id: string) => store().grids.find((g) => g.id === id)!.levels;

  it("输入 0.45 点「添加」→ 进 levels,列表里出现勾选框", () => {
    const { dock, id } = openExpanded();
    fireEvent.change(ratioInput(dock), { target: { value: "0.45" } });
    fireEvent.click(addBtn(dock));

    expect(levelsOf(id)).toContain(0.45);
    expect(store().grids[0].levels).toContain(0.45);
    const custom = dock.querySelector(".kf-draw-level.custom") as HTMLElement;
    expect(custom.textContent).toContain("0.45");
    // 输入框清空,可以接着加下一条
    expect(ratioInput(dock).value).toBe("");
    expect(errText(dock)).toBe("");
  });

  it("回车也能提交", () => {
    const { dock, id } = openExpanded();
    fireEvent.change(ratioInput(dock), { target: { value: "0.66" } });
    fireEvent.keyDown(ratioInput(dock), { key: "Enter" });
    expect(levelsOf(id)).toContain(0.66);
  });

  it("负数/大于 1 的延伸比例也接受", () => {
    const { dock, id } = openExpanded();
    fireEvent.change(ratioInput(dock), { target: { value: "-0.5" } });
    fireEvent.click(addBtn(dock));
    fireEvent.change(ratioInput(dock), { target: { value: "2.5" } });
    fireEvent.click(addBtn(dock));
    expect(levelsOf(id)).toEqual(expect.arrayContaining([-0.5, 2.5]));
  });

  it("空输入 → 「请输入比例」,不动 levels", () => {
    const { dock, id } = openExpanded();
    const before = [...levelsOf(id)];
    fireEvent.change(ratioInput(dock), { target: { value: "   " } });
    fireEvent.click(addBtn(dock));
    expect(errText(dock)).toBe("请输入比例");
    expect(levelsOf(id)).toEqual(before);
  });

  it("非数字 → 「比例必须是数字」,不动 levels", () => {
    const { dock, id } = openExpanded();
    const before = [...levelsOf(id)];
    fireEvent.change(ratioInput(dock), { target: { value: "abc" } });
    fireEvent.click(addBtn(dock));
    expect(errText(dock)).toBe("比例必须是数字");
    expect(levelsOf(id)).toEqual(before);
  });

  it("已存在的档位 → 「该档位已存在」,不重复插入", () => {
    const { dock, id } = openExpanded();
    fireEvent.change(ratioInput(dock), { target: { value: "0.5" } });
    fireEvent.click(addBtn(dock));
    expect(errText(dock)).toBe("该档位已存在");
    expect(levelsOf(id).filter((lv) => lv === 0.5)).toHaveLength(1);
  });

  it("× 删掉自定义档位;固定档位没有 × 按钮", () => {
    const { dock, id } = openExpanded();
    fireEvent.change(ratioInput(dock), { target: { value: "0.45" } });
    fireEvent.click(addBtn(dock));

    const custom = dock.querySelector(".kf-draw-level.custom") as HTMLElement;
    const del = custom.querySelector(".kf-draw-level-del") as HTMLElement;
    expect(del.getAttribute("aria-label")).toBe("移除档位 0.45");
    fireEvent.click(del);

    expect(levelsOf(id)).not.toContain(0.45);
    // 删除按钮在 checkbox 的 label 里,不能顺手把档位又勾回来
    expect(store().grids[0].levels).not.toContain(0.45);
    expect(dock.querySelector(".kf-draw-level.custom")).toBeNull();
  });

  it("× 不改变这一行的选中态(事件不冒泡到行)", () => {
    const { dock, id } = openExpanded();
    act(() => store().clearSelection());
    fireEvent.change(ratioInput(dock), { target: { value: "0.45" } });
    fireEvent.click(addBtn(dock));
    fireEvent.click(dock.querySelector(".kf-draw-level-del") as HTMLElement);
    expect(store().selectedIds.has(id)).toBe(false);
  });

  it("删光自定义档位后,固定档位仍在(不是恢复默认,是精确删除)", () => {
    const { dock, id } = openExpanded();
    for (const v of ["0.45", "0.55"]) {
      fireEvent.change(ratioInput(dock), { target: { value: v } });
      fireEvent.click(addBtn(dock));
    }
    act(() => {
      store().removeLevelOf(id, 0.45);
      store().removeLevelOf(id, 0.55);
    });
    expect(levelsOf(id)).not.toContain(0.45);
    expect(levelsOf(id)).not.toContain(0.55);
    expect(levelsOf(id)).toContain(0.5);
    expect(dock.querySelectorAll(".kf-draw-level").length).toBeGreaterThan(5);
  });
});
