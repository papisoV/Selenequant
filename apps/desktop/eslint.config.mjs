// 直接 import eslint-config-next 的 flat config 数组 (它本就是 flat 格式)。
// 不用 FlatCompat:compat.extends 会走 @eslint/eslintrc config-validator,
// 对 eslint-plugin-react 的循环引用对象做 JSON.stringify → 崩 (circular JSON)。
// 直接展开绕过 legacy 校验路径。
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";
import reactControlledInput from "./eslint-rules/react-controlled-input.mjs";

const eslintConfig = [
  ...nextCoreWebVitals,
  ...nextTypescript,
  {
    plugins: {
      // 本地自定义 plugin,不经 npm。防御 value= 无 onChange 的受控组件误用。
      // eslint-config-next 已 bundle eslint-plugin-react,但它 103 条规则里没有
      // "value= requires onChange"(只有 checked-requires-onchange-or-readonly)。
      // 见 ./eslint-rules/react-controlled-input.mjs 顶部注释(事故溯源 + 为何不用 no-restricted-syntax)。
      "selene-react": reactControlledInput,
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
      // useApi → useApiPage 迁移:warn 级,43 页存量渐进迁移,不爆 error
      "no-restricted-imports": ["warn", {
        patterns: [{
          group: ["@/lib/hooks/use-api"],
          message: "Use useApiPage from @/lib/hooks/use-api-page instead. distinguish=true/false explicit.",
          allowTypeImports: false,
        }],
      }],
      // 防御 grid-page.tsx:109 事故:value= 无 onChange → React 渲染只读字段。
      // 自定义 rule 精确判断兄弟属性缺失(OnChange/defaultValue/readOnly 都无才报),
      // 零误报。no-restricted-syntax 做不到(全项目 65 误报)。
      "selene-react/value-requires-onchange": "error",
    },
  },
];

export default eslintConfig;
