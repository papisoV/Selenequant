// 自定义 ESLint plugin:防御 React 受控组件 value= 误用。
// 起源:grid-page.tsx:109 用 value= + 只有 onBlur 无 onChange,
// React 渲染只读字段,console 报 "You provided a `value` prop without an `onChange` handler"。
//
// 为什么不用 no-restricted-syntax:esquery selector 无法表达"有 value 但无 onChange",
// 会命中所有 value=(实测全项目 65 个误报),噪声淹没信号,开发者会无视。
// 自定义 rule 能访问 AST 节点的兄弟属性,精确判断 onChange/defaultValue/readOnly 缺失。
//
// 这是 bug-scope-checklist 第 7 类(框架 API 契约误用)的防御 ——
// tsc/ESLint 看 value=+onBlur 语法合法,React 运行时才报错,静态层抓不到。
// 本 rule 把运行时警告提到静态检查阶段。

const CONTROLLED_INPUT_TAGS = new Set(["input", "select", "textarea"]);
const COMPANION_ATTRS = new Set(["onChange", "defaultValue", "defaultChecked", "readOnly"]);

/**
 * @type {import('eslint').RuleModule}
 */
const valueRequiresOnChange = {
  meta: {
    type: "problem",
    docs: {
      description: "受控组件 value= 必须配对 onChange=,否则 React 渲染只读字段",
    },
    schema: [],
    messages: {
      missing: "受控组件 <{{tag}}> 有 value= 但无 onChange= (React 会渲染只读字段)。加 onChange=,或改用 defaultValue=,或加 readOnly。",
    },
  },
  create(context) {
    return {
      JSXOpeningElement(node) {
        const nameNode = node.name;
        if (nameNode.type !== "JSXIdentifier") return;
        if (!CONTROLLED_INPUT_TAGS.has(nameNode.name)) return;

        // 找 value 属性(含 value={...}),跳过 spread 属性 {...props} —— 无法静态判断。
        const valueAttr = node.attributes.find(
          (attr) =>
            attr.type === "JSXAttribute" &&
            attr.name.type === "JSXIdentifier" &&
            attr.name.name === "value"
        );
        if (!valueAttr) return;

        // value="" 字面量也算受控,但 checkbox/radio 的 value 不触发受控警告(checked 才是)。
        // 这里仍报 —— 传 value= 字面量又无 onChange 通常也是误用。

        // 检查兄弟属性:有任一 companion 即豁免。
        const hasCompanion = node.attributes.some(
          (attr) =>
            attr.type === "JSXAttribute" &&
            attr.name.type === "JSXIdentifier" &&
            COMPANION_ATTRS.has(attr.name.name)
        );
        if (hasCompanion) return;

        // 无任何 companion —— 真 bug,报。
        context.report({
          node: valueAttr,
          messageId: "missing",
          data: { tag: nameNode.name },
        });
      },
    };
  },
};

export default {
  rules: {
    "value-requires-onchange": valueRequiresOnChange,
  },
};
