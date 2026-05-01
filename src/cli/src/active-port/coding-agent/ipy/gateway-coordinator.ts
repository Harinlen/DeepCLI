// @ts-nocheck
export function getPythonGatewayCoordinator() { return { list: () => [], stop: async () => {} }; }
export function getGatewayStatus() { return { running: false, jobs: [] }; }
export async function acquireSharedGateway() { return undefined; }
export async function releaseSharedGateway() {}
export async function shutdownSharedGateway() {}
export class GatewayCoordinator {}
