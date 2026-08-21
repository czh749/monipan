<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { gsap } from 'gsap'
import type { AuthRequest } from '../types'

const props = defineProps<{
  loading: boolean
  error: string
}>()

const emit = defineEmits<{
  authenticate: [mode: 'login' | 'register', payload: AuthRequest]
}>()

const mode = ref<'login' | 'register'>('login')
const gatewayRoot = ref<HTMLElement | null>(null)
const sceneImage = ref<HTMLElement | null>(null)
const sceneGlow = ref<HTMLElement | null>(null)
const username = ref('')
const password = ref('')
const confirmation = ref('')
const inviteCode = ref('')
const localError = ref('')
const title = computed(() => mode.value === 'login' ? '进入交易席位' : '创建模拟账户')
const actionLabel = computed(() => mode.value === 'login' ? '登录交易席位' : '创建并进入')
let motionMedia: ReturnType<typeof gsap.matchMedia> | undefined
let modeTween: gsap.core.Tween | undefined

watch(mode, async () => {
  localError.value = ''
  password.value = ''
  confirmation.value = ''
  inviteCode.value = ''

  await nextTick()
  const fields = gatewayRoot.value?.querySelectorAll('.auth-form label')
  if (!fields?.length || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  modeTween?.kill()
  modeTween = gsap.fromTo(
    fields,
    { autoAlpha: 0, y: 10 },
    { autoAlpha: 1, y: 0, duration: .32, stagger: .045, ease: 'power2.out', clearProps: 'all' },
  )
})

onMounted(() => {
  if (!gatewayRoot.value) return
  motionMedia = gsap.matchMedia()
  motionMedia.add(
    {
      desktop: '(min-width: 681px)',
      reduceMotion: '(prefers-reduced-motion: reduce)',
    },
    (context) => {
      const { desktop, reduceMotion } = context.conditions as { desktop: boolean; reduceMotion: boolean }
      if (reduceMotion) {
        gsap.set(['.auth-market-context > *', '.auth-pass', '.auth-live-rail'], { autoAlpha: 1, clearProps: 'transform' })
        return
      }

      const entrance = gsap.timeline({ defaults: { ease: 'power3.out' } })
      entrance
        .fromTo(sceneImage.value, { scale: 1.09 }, { scale: 1.035, duration: 1.8 }, 0)
        .fromTo('.auth-live-rail', { autoAlpha: 0, scaleX: .7 }, { autoAlpha: 1, scaleX: 1, duration: .75 }, .18)
        .fromTo('.auth-market-context > *', { autoAlpha: 0, y: 24 }, { autoAlpha: 1, y: 0, duration: .7, stagger: .08 }, .28)
        .fromTo('.auth-pass', { autoAlpha: 0, x: desktop ? 48 : 0, y: desktop ? 0 : 24 }, { autoAlpha: 1, x: 0, y: 0, duration: .82 }, .44)

      if (!desktop || !sceneImage.value || !sceneGlow.value || !gatewayRoot.value) return
      const imageX = gsap.quickTo(sceneImage.value, 'x', { duration: 1.1, ease: 'power3.out' })
      const imageY = gsap.quickTo(sceneImage.value, 'y', { duration: 1.1, ease: 'power3.out' })
      const glowX = gsap.quickTo(sceneGlow.value, 'x', { duration: .7, ease: 'power3.out' })
      const glowY = gsap.quickTo(sceneGlow.value, 'y', { duration: .7, ease: 'power3.out' })
      const root = gatewayRoot.value

      const moveScene = (event: PointerEvent) => {
        const xRatio = event.clientX / Math.max(window.innerWidth, 1) - .5
        const yRatio = event.clientY / Math.max(window.innerHeight, 1) - .5
        imageX(xRatio * -18)
        imageY(yRatio * -12)
        glowX(event.clientX - root.clientWidth * .5)
        glowY(event.clientY - root.clientHeight * .5)
      }

      root.addEventListener('pointermove', moveScene, { passive: true })
      return () => root.removeEventListener('pointermove', moveScene)
    },
    gatewayRoot.value,
  )
})

onUnmounted(() => {
  modeTween?.kill()
  motionMedia?.revert()
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
    ...(mode.value === 'register' ? { invite_code: inviteCode.value.trim() } : {}),
  })
}
</script>

<template>
  <section ref="gatewayRoot" class="auth-gateway" aria-labelledby="auth-title">
    <div class="auth-scene" aria-hidden="true">
      <div ref="sceneImage" class="auth-scene-image"></div>
      <div class="auth-scene-grid"></div>
      <div ref="sceneGlow" class="auth-scene-glow"></div>
      <div class="auth-scene-vignette"></div>
      <div class="auth-live-rail">
        <i></i>
        <span>MARKET PRACTICE ENVIRONMENT</span>
        <b>SIMULATION ONLINE</b>
      </div>
    </div>

    <div class="auth-market-context" aria-hidden="true">
      <span class="section-kicker">SECURE MARKET ACCESS</span>
      <strong>把每一次判断，<br>放进真实行情验证。</strong>
      <p>进入你的个人模拟交易席位，在公开行情环境中练习选股、委托与复盘。</p>
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
        <label v-if="mode === 'register'">
          <span>一次性邀请码</span>
          <input
            v-model="inviteCode"
            name="invite-code"
            autocomplete="off"
            minlength="16"
            maxlength="128"
            placeholder="输入朋友发给你的邀请码"
            spellcheck="false"
            required
            :disabled="loading"
          >
        </label>
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
        {{ mode === 'register' ? '邀请码仅可使用一次；密码经 Argon2id 哈希保存。' : '密码经 Argon2id 哈希保存；登录凭证不会暴露给页面脚本。' }}
      </p>
    </div>
  </section>
</template>
