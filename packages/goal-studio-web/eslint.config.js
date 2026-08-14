// ESLint flat config (ESLint 9+) — goal-studio web
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/", "dist-app/", "node_modules/", "*.config.ts", "*.config.js"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    rules: {
      // 生产代码禁止调试输出(console.error 保留用于错误上报)
      "no-console": ["warn", { allow: ["error", "warn"] }],
      // 允许下划线前缀的未使用变量
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
);
