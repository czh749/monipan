import { gsap } from 'gsap'
import { nextTick, onBeforeUnmount, ref, watch, type Ref } from 'vue'

export function useTradingScene(enabled: Readonly<Ref<boolean>>) {
  const appShellRoot = ref<HTMLElement | null>(null)
  const tradingBackdrop = ref<HTMLElement | null>(null)
  const tradingGlow = ref<HTMLElement | null>(null)
  let tradingStageMotion: ReturnType<typeof gsap.matchMedia> | null = null

  function stopTradingStage() {
    tradingStageMotion?.revert()
    tradingStageMotion = null
  }

  async function animateTradingStage() {
    await nextTick()
    stopTradingStage()

    const root = appShellRoot.value
    const backdrop = tradingBackdrop.value
    const glow = tradingGlow.value
    if (!root || !backdrop || !glow || !enabled.value) return

    tradingStageMotion = gsap.matchMedia()
    tradingStageMotion.add(
      {
        desktop: '(min-width: 921px)',
        compact: '(max-width: 920px)',
        reduceMotion: '(prefers-reduced-motion: reduce)',
      },
      (context) => {
        const { desktop, reduceMotion } = context.conditions as { desktop: boolean; reduceMotion: boolean }
        const panels = root.querySelectorAll<HTMLElement>('.market-tape, .index-board-head, .index-node, .workspace > *')
        const beams = root.querySelectorAll<HTMLElement>('.cockpit-beam')

        if (reduceMotion) {
          gsap.set([backdrop, ...panels, ...beams], { autoAlpha: 1, clearProps: 'transform' })
          return
        }

        const entrance = gsap.timeline({ defaults: { ease: 'power3.out' } })
        entrance
          .fromTo(backdrop, { autoAlpha: .35, scale: 1.075 }, { autoAlpha: 1, scale: 1.025, duration: 1.45 }, 0)
          .fromTo(beams, { autoAlpha: 0, scaleX: 0, transformOrigin: 'left center' }, { autoAlpha: .7, scaleX: 1, duration: .68, stagger: .08 }, .12)
          .fromTo('.market-tape', { autoAlpha: 0, y: -14 }, { autoAlpha: 1, y: 0, duration: .44, clearProps: 'transform,opacity,visibility' }, .18)
          .fromTo('.index-board-head', { autoAlpha: 0, y: 16 }, { autoAlpha: 1, y: 0, duration: .48, clearProps: 'transform,opacity,visibility' }, .29)
          .fromTo('.index-node', { autoAlpha: 0, y: 22, scale: .985 }, { autoAlpha: 1, y: 0, scale: 1, duration: .48, stagger: .055, clearProps: 'transform,opacity,visibility' }, .38)
          .fromTo('.workspace > *', { autoAlpha: 0, y: 26 }, { autoAlpha: 1, y: 0, duration: .58, stagger: .1, clearProps: 'transform,opacity,visibility' }, .54)

        if (!desktop) return
        const backdropX = gsap.quickTo(backdrop, 'x', { duration: 1.15, ease: 'power3.out' })
        const backdropY = gsap.quickTo(backdrop, 'y', { duration: 1.15, ease: 'power3.out' })
        const glowX = gsap.quickTo(glow, 'x', { duration: .72, ease: 'power3.out' })
        const glowY = gsap.quickTo(glow, 'y', { duration: .72, ease: 'power3.out' })

        const moveStage = (event: PointerEvent) => {
          const xRatio = event.clientX / Math.max(window.innerWidth, 1) - .5
          const yRatio = event.clientY / Math.max(window.innerHeight, 1) - .5
          backdropX(xRatio * -16)
          backdropY(yRatio * -10)
          glowX(event.clientX - window.innerWidth * .5)
          glowY(event.clientY - window.innerHeight * .5)
        }

        window.addEventListener('pointermove', moveStage, { passive: true })
        return () => window.removeEventListener('pointermove', moveStage)
      },
      root,
    )
  }

  watch(enabled, (active) => {
    if (!active) stopTradingStage()
  }, { flush: 'sync' })
  onBeforeUnmount(stopTradingStage)

  return { appShellRoot, tradingBackdrop, tradingGlow, animateTradingStage }
}
