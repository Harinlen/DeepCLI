// seam 1: connect + authenticate
import { connect } from "./helpers.js";

const client = await connect();
await client.close();
console.log("PASS: connect + auth");
