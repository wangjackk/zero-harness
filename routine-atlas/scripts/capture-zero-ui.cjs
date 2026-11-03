/* Capture real Zero Agent UI evidence without changing application state unnecessarily.
   Usage: PLAYWRIGHT_MODULE=<absolute module path> node scripts/capture-zero-ui.cjs <inspect|new|skill|xml|dag> */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const path = require('node:path')

const action = process.argv[2] || 'inspect'
const root = path.resolve(__dirname, '..')
const shot = (name) => path.join(root, '截图', name)

async function main() {
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const page = await browser.newPage({ viewport: { width: 1800, height: 1100 }, deviceScaleFactor: 1 })
  await page.goto('http://localhost:5250', { waitUntil: 'networkidle' })
  await page.waitForTimeout(1800)

  if (action === 'inspect') {
    const sidebar = await page.locator('.sidebar-list').innerText()
    const body = await page.locator('body').innerText()
    console.log('SIDEBAR\n' + sidebar)
    console.log('BODY\n' + body.slice(0, 12000))
    await page.screenshot({ path: shot('_inspect-current.png'), fullPage: true })
  }

  if (action === 'new' || action === 'skill') {
    await page.locator('.sidebar-new').click()
    await page.waitForTimeout(1400)
    if (action === 'new') {
      console.log((await page.locator('body').innerText()).slice(0, 10000))
      await page.screenshot({ path: shot('_inspect-new.png'), fullPage: true })
    } else {
      const chip = page.locator('.sk-chip', { hasText: 'routine-bridge' }).first()
      const sdkChip = page.locator('.sk-chip', { hasText: 'routine-sdk' }).first()
      await chip.locator('.sk-chip-main').click()
      await sdkChip.locator('.sk-chip-main').click()
      await sdkChip.locator('.sk-level').click()
      await page.waitForTimeout(350)
      await page.screenshot({ path: shot('skill-library-list.png'), fullPage: true })
      await chip.scrollIntoViewIfNeeded()
      await chip.locator('.sk-view').click()
      await page.locator('.skd-content').waitFor({ state: 'visible', timeout: 15000 })
      await page.waitForTimeout(600)
      await page.screenshot({ path: shot('skill-shared-library.png'), fullPage: true })
    }
  }

  if (action === 'xml') {
    const prompt = page.getByText('边放音乐边做', { exact: true }).first()
    await prompt.scrollIntoViewIfNeeded()
    const tool = prompt.locator('xpath=following::div[contains(@class,"msg") and contains(@class,"tool")][1]')
    await tool.locator('summary.tool-header').click()
    await page.waitForTimeout(500)
    console.log((await page.locator('.messages:visible').first().innerText()).slice(-8000))
    await page.screenshot({ path: shot('xml-stream-actions.png'), fullPage: true })
  }

  if (action === 'dag') {
    await page.locator('button[title="DAG 工作流"]').click()
    await page.locator('.dag-panel').waitFor({ state: 'visible' })
    await page.waitForTimeout(900)
    console.log((await page.locator('.dag-panel').innerText()).slice(0, 9000))
    await page.screenshot({ path: shot('_inspect-dag.png'), fullPage: true })
  }

  await browser.close()
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
