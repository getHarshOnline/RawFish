/**
 * createPoller + Vue lifecycle: teardown contract.
 *
 * Components that poll backend state should follow the same shape:
 *
 *   const poller = createPoller({...})
 *   onMounted(poller.start)
 *   onUnmounted(poller.stop)
 *
 * The contract that matters for review is "unmount halts the
 * poller AND aborts any in-flight tick". This test pins that
 * shape against a minimal Vue component so a future refactor that
 * forgets the onUnmounted hook fails loudly here.
 */
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h, onMounted, onUnmounted } from 'vue'
import { createPoller } from '../usePoller.js'

function makeProbe () {
  const calls = []
  let aborted = false
  const poller = createPoller({
    fn: async ({ signal, token }) => {
      calls.push(token)
      signal.addEventListener('abort', () => { aborted = true })
      // Long-running fake fetch -- gives the unmount a chance to
      // abort mid-tick.
      await new Promise((resolve) => setTimeout(resolve, 5000))
    },
    intervalMs: 100,
  })
  return { poller, calls, getAborted: () => aborted }
}

describe('createPoller lifecycle', () => {
  it('starts on mount and stops on unmount', async () => {
    vi.useFakeTimers()
    const probe = makeProbe()
    const Component = defineComponent({
      setup () {
        onMounted(() => probe.poller.start())
        onUnmounted(() => probe.poller.stop())
        return () => h('div')
      },
    })
    const wrapper = mount(Component)
    // Flush the start() setTimeout(0) so the first tick fires.
    await vi.advanceTimersByTimeAsync(0)
    expect(probe.poller.isRunning()).toBe(true)
    expect(probe.calls.length).toBe(1)

    wrapper.unmount()
    expect(probe.poller.isRunning()).toBe(false)
    expect(probe.getAborted()).toBe(true)
    vi.useRealTimers()
  })

  it('does not fire additional ticks after unmount', async () => {
    vi.useFakeTimers()
    const probe = makeProbe()
    const Component = defineComponent({
      setup () {
        onMounted(() => probe.poller.start())
        onUnmounted(() => probe.poller.stop())
        return () => h('div')
      },
    })
    const wrapper = mount(Component)
    await vi.advanceTimersByTimeAsync(0)
    const callsAtUnmount = probe.calls.length

    wrapper.unmount()
    // Try to provoke more ticks. None should fire.
    await vi.advanceTimersByTimeAsync(10000)
    expect(probe.calls.length).toBe(callsAtUnmount)
    vi.useRealTimers()
  })

  it('bumps currentToken on stop so stale tick commits short-circuit', async () => {
    vi.useFakeTimers()
    let tokenAtTickStart = null
    const poller = createPoller({
      fn: async ({ token }) => {
        tokenAtTickStart = token
        await new Promise((resolve) => setTimeout(resolve, 500))
      },
      intervalMs: 100,
    })
    poller.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(tokenAtTickStart).not.toBeNull()
    poller.stop()
    // After stop, the in-flight tick's token MUST differ from
    // currentToken so a token-gated commit drops itself.
    expect(tokenAtTickStart).not.toBe(poller.currentToken())
    vi.useRealTimers()
  })
})
