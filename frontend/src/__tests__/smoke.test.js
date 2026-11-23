/**
 * Vitest infrastructure smoke test.
 *
 * Proves three orthogonal things in one short suite so a CI failure
 * pinpoints which infra layer broke:
 *
 *   1. Vitest itself can collect + run a test (the bare `expect`).
 *   2. happy-dom is wired (any `document` access works).
 *   3. @vue/test-utils mounts a single-file Vue component and
 *      assertions against its rendered text pass.
 *
 * Lives at the project root under ``src/__tests__/`` so a future
 * developer adding their first component test can copy the
 * ``mount(...)`` shape without hunting through a docs site.
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'

describe('frontend test infra smoke', () => {
  it('vitest runs and expect works', () => {
    expect(2 + 2).toBe(4)
  })

  it('happy-dom provides a document', () => {
    const div = document.createElement('div')
    div.textContent = 'hello'
    expect(div.textContent).toBe('hello')
  })

  it('vue-test-utils can mount a Vue component', () => {
    const HelloPolicy = defineComponent({
      props: { who: { type: String, default: 'policy lane' } },
      render() {
        return h('span', { class: 'greeting' }, `Hello, ${this.who}.`)
      }
    })

    const wrapper = mount(HelloPolicy, { props: { who: 'RawFish' } })

    expect(wrapper.find('.greeting').exists()).toBe(true)
    expect(wrapper.text()).toBe('Hello, RawFish.')
  })
})
