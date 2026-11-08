import { describe, expect, it } from 'vitest'

import { applyCustomTheme, checkContrast, clearCustomTheme, contrastRatio, validateTheme } from '../../src/themes/custom'

describe('custom theme schema', () => {
  it('accepts valid themes', () => {
    const out = validateTheme({ name: 'x', dark: true, tokens: { '--primary': '#fff' } })
    expect(out.error).toBeUndefined()
    expect(out.theme?.tokens['--primary']).toBe('#fff')
  })

  it('rejects invalid token names and missing fields', () => {
    expect(validateTheme({ tokens: {} }).error).toContain('name')
    expect(validateTheme({ name: 'x' }).error).toContain('tokens')
    expect(validateTheme({ name: 'x', tokens: { primary: '#fff' } }).error).toContain('token')
  })

  it('applies and clears css variables', () => {
    const theme = validateTheme({ name: 'x', dark: true, tokens: { '--primary': '#123456' } }).theme!
    applyCustomTheme(theme)
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(document.documentElement.style.getPropertyValue('--primary')).toBe('#123456')
    clearCustomTheme()
    expect(document.documentElement.style.getPropertyValue('--primary')).toBe('')
  })

  it('flags low contrast', () => {
    expect(contrastRatio('#ffffff', '#000000')).toBeGreaterThan(20)
    const theme = validateTheme({
      name: 'x',
      tokens: { '--text': '#777777', '--bg': '#888888' },
    }).theme!
    expect(checkContrast(theme)[0]).toContain('对比度')
  })
})
