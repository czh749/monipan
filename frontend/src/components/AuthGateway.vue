<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { AuthRequest } from '../types'

const props = defineProps<{
  loading: boolean
  error: string
}>()

const emit = defineEmits<{
  authenticate: [mode: 'login' | 'register', payload: AuthRequest]
}>()

const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const confirmation = ref('')
const localError = ref('')
const title = computed(() => mode.value === 'login' ? '进入交易席位' : '创建模拟账户')
const actionLabel = computed(() => mode.value === 'login' ? '登录交易席位' : '创建并进入')

watch(mode, () => {
  localError.value = ''
  password.value = ''
  confirmation.value = ''
})

function submit() {
  localError.value = ''
  if (mode.value === 'register' && password.value !== confirmation.value) {
    localError.value = '两次输入的密码不一致'
    return
  }
  emit('authenticate', mode.value, {
    username: username.value.trim(),
    password: password.value,
  })
}
</script>

<template>
  <section class="auth-gateway" aria-labelledby="auth-title">
    <div class="auth-market-context" aria-hidden="true">
      <span class="section-kicker">SECURE MARKET ACCESS</span>
      <strong>个人模拟交易席位</strong>
      <p>独立资金、持仓、自选与委托记录，只属于当前登录账户。</p>
      <div class="auth-ledger">
        <div><span>初始资金</span><b>¥ 1,000,000</b></div>
        <div><span>交易市场</span><b>A 股模拟盘</b></div>
        <div><span>账户隔离</span><b>已启用</b></div>
      </div>
      <small>真实公开行情 · 模拟成交 · 不构成投资建议</small>
    </div>

    <div class="auth-pass">
      <div class="auth-pass-head">
        <div>
          <span>TRADING DESK PASS</span>
          <h1 id="auth-title">{{ title }}</h1>
        </div>
        <span class="auth-pass-code">MP / 01</span>
      </div>

      <div class="auth-tabs" role="tablist" aria-label="认证方式">
        <button
          type="button"
          role="tab"
          :aria-selected="mode === 'login'"
          :class="{ active: mode === 'login' }"
          @click="mode = 'login'"
        >已有账户</button>
        <button
          type="button"
          role="tab"
          :aria-selected="mode === 'register'"
          :class="{ active: mode === 'register' }"
          @click="mode = 'register'"
        >注册账户</button>
      </div>

      <form class="auth-form" @submit.prevent="submit">
        <label>
          <span>用户名</span>
          <input
            v-model="username"
            name="username"
            autocomplete="username"
            minlength="3"
            maxlength="30"
            placeholder="中文、字母、数字或下划线"
            required
            :disabled="loading"
          >
        </label>
        <label>
          <span>密码</span>
          <input
            v-model="password"
            name="password"
            type="password"
            :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
            :minlength="mode === 'register' ? 10 : 1"
            maxlength="128"
            :placeholder="mode === 'login' ? '输入账户密码' : '至少 10 位，包含字母和数字'"
            required
            :disabled="loading"
          >
        </label>
        <label v-if="mode === 'register'">
          <span>确认密码</span>
          <input
            v-model="confirmation"
            name="password-confirmation"
            type="password"
            autocomplete="new-password"
            minlength="10"
            maxlength="128"
            placeholder="再次输入密码"
            required
            :disabled="loading"
          >
        </label>

        <p v-if="localError || props.error" class="auth-error" role="alert">
          {{ localError || props.error }}
        </p>
        <button class="auth-submit" type="submit" :disabled="loading">
          <span>{{ loading ? '正在验证…' : actionLabel }}</span>
          <b aria-hidden="true">→</b>
        </button>
      </form>

      <p class="auth-security-note">
        密码经 Argon2id 哈希保存；登录凭证不会暴露给页面脚本。
      </p>
    </div>
  </section>
</template>
