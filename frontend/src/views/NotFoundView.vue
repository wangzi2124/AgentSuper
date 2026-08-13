<template>
  <div class="not-found">
    <h1>404</h1>
    <p>页面不存在</p>
    <router-link to="/chat">前往对话</router-link>
  </div>
</template>

<style scoped>
.not-found {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 60vh;
  color: var(--text-secondary);
}
.not-found h1 {
  font-size: 4rem;
  margin: 0;
  color: var(--text-primary);
}
.not-found a {
  margin-top: 16px;
  color: var(--primary);
}
</style>
