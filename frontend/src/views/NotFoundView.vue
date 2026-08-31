<template>
  <div class="not-found">
    <div class="code">4<span class="orb">0</span>4</div>
    <p class="msg">页面不存在</p>
    <p class="sub">你访问的页面可能已被移动或删除</p>
    <router-link to="/multi-agent" class="back">前往对话</router-link>
  </div>
</template>

<style scoped>
.not-found {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
  color: var(--text-secondary);
  text-align: center;
  animation: fadeSlideUp 0.4s var(--ease);
}
.code {
  font-size: 6rem;
  font-weight: 800;
  letter-spacing: -0.03em;
  line-height: 1;
  color: var(--text);
  font-family: 'JetBrains Mono', Consolas, monospace;
}
.orb {
  display: inline-block;
  background: linear-gradient(135deg, var(--primary), var(--accent));
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
.msg { margin-top: 12px; font-size: 20px; font-weight: 600; color: var(--text); }
.sub { margin-top: 6px; font-size: 14px; color: var(--text-secondary); }
.back {
  margin-top: 24px;
  padding: 10px 22px;
  border-radius: var(--radius-pill);
  background: linear-gradient(135deg, var(--primary), color-mix(in srgb, var(--primary) 78%, var(--accent)));
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  text-decoration: none;
  box-shadow: 0 4px 14px var(--primary-glow);
  transition: transform 0.1s var(--ease-spring);
}
.back:hover { transform: translateY(-1px); }
</style>
