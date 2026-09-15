<template>
  <footer class="app-footer">
    <div class="container">
      <div class="app-footer__warning">
        <span class="app-footer__age" role="img" aria-label="Только для лиц старше 18 лет">18+</span>
        <p class="app-footer__warning-text">Чрезмерное употребление алкоголя вредит вашему здоровью</p>
      </div>
      <p class="app-footer__note">
        Сведения о винах из каталога «Своё вино» носят информационный характер. Сервис предназначен для лиц старше 18 лет.
      </p>
    </div>
  </footer>
</template>

<style scoped>
.app-footer {
  flex: none;
  margin-top: 16px;
  padding: 20px 0 24px;
  border-top: 1px solid var(--c-border-soft);
}

.app-footer__warning {
  display: flex;
  align-items: center;
  gap: 12px;
}

.app-footer__age {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border: 2px solid var(--c-text);
  border-radius: 50%;
  font-size: 15px;
  font-weight: 700;
  line-height: 1;
}

.app-footer__warning-text {
  font-size: 15px;
  font-weight: 600;
  line-height: 20px;
}

.app-footer__note {
  margin-top: 12px;
  color: var(--c-text-secondary);
  font-size: 13px;
  line-height: 18px;
}
</style>
