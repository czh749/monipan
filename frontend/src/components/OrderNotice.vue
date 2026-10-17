<script setup lang="ts">
import { gsap } from 'gsap'
import { onBeforeUnmount } from 'vue'
import type { OrderNotice } from '../composables/useOrderEntry'

defineProps<{ notice: OrderNotice | null }>()
const emit = defineEmits<{ close: [] }>()
let noticeMotion: gsap.core.Animation | null = null

function enterOrderNotice(element: Element, done: () => void) {
  const target = element as HTMLElement
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const parts = target.querySelectorAll<HTMLElement>('.order-notice-mark, .order-notice-copy, button')
  const signal = target.querySelector<HTMLElement>('.order-notice-scan')
  gsap.killTweensOf([target, ...parts, signal].filter(Boolean))

  if (reducedMotion) {
    gsap.set(target, { autoAlpha: 1 })
    done()
    return
  }

  noticeMotion?.kill()
  noticeMotion = gsap.timeline({
    defaults: { ease: 'power2.out' },
    onComplete: () => {
      gsap.set(target, { clearProps: 'transform,opacity,visibility' })
      done()
    },
  })
    .fromTo(target, { autoAlpha: 0, y: -10 }, { autoAlpha: 1, y: 0, duration: 0.18 })
    .fromTo(parts, { autoAlpha: 0, x: 6 }, { autoAlpha: 1, x: 0, duration: 0.16, stagger: 0.025 }, 0.045)
    .fromTo(signal, { scaleX: 0, transformOrigin: 'left center' }, { scaleX: 1, duration: 0.28 }, 0)
}

function leaveOrderNotice(element: Element, done: () => void) {
  const target = element as HTMLElement
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  gsap.killTweensOf(target)
  noticeMotion?.kill()
  noticeMotion = gsap.to(target, {
    autoAlpha: 0,
    y: reducedMotion ? 0 : -6,
    duration: reducedMotion ? 0 : 0.14,
    ease: 'power1.in',
    onComplete: done,
  })
}

onBeforeUnmount(() => noticeMotion?.kill())
</script>

<template>
  <Transition :css="false" @enter="enterOrderNotice" @leave="leaveOrderNotice">
    <aside
      v-if="notice"
      class="order-notice"
      :class="`order-notice-${notice.kind}`"
      :role="notice.kind === 'error' ? 'alert' : 'status'"
      aria-live="polite"
    >
      <span class="order-notice-scan" aria-hidden="true"></span>
      <span class="order-notice-mark" aria-hidden="true">
        {{ notice.kind === 'success' ? '✓' : '!' }}
      </span>
      <div class="order-notice-copy">
        <span class="order-notice-kicker">
          {{ notice.kind === 'success' ? 'ORDER FILLED' : 'ORDER REJECTED' }}
        </span>
        <strong>{{ notice.title }}</strong>
        <p>{{ notice.message }}</p>
        <small>{{ notice.meta }}</small>
      </div>
      <button type="button" aria-label="关闭下单提示" @click="emit('close')">×</button>
    </aside>
  </Transition>
</template>
