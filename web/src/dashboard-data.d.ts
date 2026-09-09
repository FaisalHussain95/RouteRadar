/* `@dashboard-data` is an alias, resolved in vite.config.ts to the pipeline's export when
   the box has written one and to web/fixtures/dashboard.json otherwise. It is declared as
   `unknown` on purpose: whichever file it lands on is validated by `src/data.ts` before
   anything reads it, so the types the page trusts are the generated ones and never the
   shape TypeScript happened to infer from whichever JSON was on disk at compile time. */
declare module "@dashboard-data" {
  const data: unknown;
  export default data;
}
