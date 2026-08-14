import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'goal-studio',
  description: '可视化 Loop 创作系统',
  lang: 'zh-CN',
  lastUpdated: true,
  cleanUrls: true,

  themeConfig: {
    nav: [
      { text: '首页', link: '/' },
      { text: '快速开始', link: '/getting-started' },
      { text: '架构', link: '/architecture' },
      { text: '适配器开发', link: '/adapter-dev' },
      { text: '部署', link: '/deployment' },
      { text: 'API', link: '/api' }
    ],

    sidebar: [
      {
        text: '入门',
        items: [
          { text: '快速开始', link: '/getting-started' },
          { text: '核心概念', link: '/concepts' },
          { text: '系统架构', link: '/architecture' }
        ]
      },
      {
        text: '指南',
        items: [
          { text: '适配器开发', link: '/adapter-dev' },
          { text: '意图解析器', link: '/intent-parser' },
          { text: '棘轮门控', link: '/ratchet-gate' }
        ]
      },
      {
        text: '参考',
        items: [
          { text: 'CLI 命令', link: '/cli' },
          { text: '生产部署', link: '/deployment' },
          { text: 'API 参考', link: '/api' }
        ]
      }
    ],

    socialLinks: [
      { icon: 'github', link: 'https://github.com/goal-studio/goal-studio' }
    ],

    footer: {
      message: '基于 MIT 协议发布',
      copyright: 'Copyright © 2024 goal-studio'
    },

    outline: {
      label: '本页目录',
      level: [2, 3]
    },

    docFooter: {
      prev: '上一页',
      next: '下一页'
    },

    lastUpdatedText: '最后更新于',

    search: {
      provider: 'local',
      options: {
        translations: {
          button: {
            buttonText: '搜索文档',
            buttonAriaLabel: '搜索文档'
          },
          modal: {
            noResultsText: '无法找到相关结果',
            resetButtonTitle: '清除查询条件',
            footer: {
              selectText: '选择',
              navigateText: '切换'
            }
          }
        }
      }
    }
  }
})
