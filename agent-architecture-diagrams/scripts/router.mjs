/** Pinned libavoid dependency. JSON input/output; no network during routing. */
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { AvoidLib } from "../node_modules/libavoid-js/dist/index-node.mjs";

await AvoidLib.load(fileURLToPath(new URL("../node_modules/libavoid-js/dist/libavoid.wasm", import.meta.url)));
const A = AvoidLib.getInstance();
const directions = { N: A.ConnDirUp, S: A.ConnDirDown, W: A.ConnDirLeft, E: A.ConnDirRight };

function route(input) {
  const router = new A.Router(A.OrthogonalRouting);
  const options = input.options || {};
  for (const [name, value] of Object.entries({
    shapeBufferDistance: options.clearance ?? 16,
    idealNudgingDistance: options.edgeGap ?? 20,
    crossingPenalty: options.crossingPenalty ?? 1000,
    fixedSharedPathPenalty: 4000,
    segmentPenalty: options.segmentPenalty ?? 35,
    reverseDirectionPenalty: 60,
  })) router.setRoutingParameter(A[name], value);
  for (const name of [
    "nudgeOrthogonalSegmentsConnectedToShapes",
    "penaliseOrthogonalSharedPathsAtConnEnds",
    "nudgeOrthogonalTouchingColinearSegments",
    "nudgeSharedPathsWithCommonEndPoint",
  ]) router.setRoutingOption(A[name], true);
  const shapes = new Map();
  const temporaries = [];
  for (const n of [...input.nodes, ...(input.obstacles || [])]) {
    const p = new A.Point(n.x, n.y);
    const q = new A.Point(n.x + n.w, n.y + n.h);
    const rect = new A.Rectangle(p, q);
    shapes.set(n.id, new A.ShapeRef(router, rect));
    temporaries.push(p, q, rect);
  }
  let pinId = 1;
  const connections = input.edges.map(e => {
    const ends = ["source", "target"].map(end => {
      const p = e[end];
      const shape = shapes.get(p.node);
      if (!shape) throw new Error(`Missing shape ${p.node}`);
      const klass = pinId++;
      for (const site of p.sites) {
        const pin = new A.ShapeConnectionPin(shape, klass, site.x, site.y, true, 0, directions[site.side]);
        pin.setExclusive(true);
        pin.setConnectionCost(site.cost || 0);
      }
      const connection = new A.ConnEnd(shape, klass);
      temporaries.push(connection);
      return connection;
    });
    const connection = new A.ConnRef(router, ...ends);
    connection.setHateCrossings(true);
    return connection;
  });
  router.processTransaction();
  const routes = connections.map((conn, i) => {
    const line = conn.displayRoute();
    const points = [];
    for (let j = 0; j < line.size(); j++) {
      const p = line.get_ps(j);
      points.push([Number(p.x.toFixed(3)), Number(p.y.toFixed(3))]);
    }
    if (points.length < 2) throw new Error(`No route for ${input.edges[i].id}`);
    return { id: input.edges[i].id, points };
  });
  // Shapes, pins and connectors belong to the router. Other wrapper objects do not.
  A.destroy(router);
  for (const object of temporaries.reverse()) A.destroy(object);
  return { routes };
}

// One process handles a batch of alternative layouts; every layout has its own router.
const input = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(Array.isArray(input) ? input.map(route) : route(input)));
