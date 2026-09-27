import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir:'./tests/browser', testMatch:'integrated-shelf.spec.ts', timeout:60_000, workers:1,
  use:{headless:true},
  projects:[
    {name:'chromium',use:{browserName:'chromium'}},
    {name:'webkit',use:{browserName:'webkit',launchOptions:process.env.SHELF_WEBKIT_EXECUTABLE?{executablePath:process.env.SHELF_WEBKIT_EXECUTABLE}:{}}},
  ],
});
