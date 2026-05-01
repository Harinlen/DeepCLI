import { assert } from "./helpers.js";
import { PermissionController } from "../src/permissions/controller.js";

assert(typeof PermissionController === "function", "PermissionController should be importable");

console.log("PASS: permission controller import");
