// The entry point is the argument, not the dashboard.
//
// This used to re-export the coverage viewer, which made the site thirteen peer
// routes with no way in: a reader had to already know which page answered their
// question. /story leads with the question instead and opens the detail inline,
// linking out to the specialist views for anyone who wants them.
export { default, metadata } from "./story/page";
