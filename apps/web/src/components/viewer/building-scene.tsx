"use client";

import * as React from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

import { TONE_HEX, statusTone } from "@/lib/utils";
import type { FloorResponse, UnitResponse } from "@/types/api";

/**
 * The 3D property viewer's scene (Request G).
 *
 * Raw Three.js rather than a React renderer: there are only two imperative
 * concerns here — a raycast against per-unit meshes and an animation loop — and
 * wrapping them in reconciler-managed components buys nothing while making the
 * hover/selection path go through React state on every mouse move.
 *
 * One mesh per unit, not one merged mesh per floor. A merged floor is cheaper to
 * draw and impossible to click, and clicking an individual flat is the whole
 * point of a vertical register.
 */

export interface SceneUnit {
  unit: UnitResponse;
  floor: FloorResponse;
  openAlerts: number;
}

const FLOOR_HEIGHT = 3.2;
const SLAB = 0.18;
const UNIT_DEPTH = 5.5;
const UNIT_WIDTH = 4.2;
const GAP = 0.35;

/** Units per row before the floor plate wraps into a second row. */
const ROW = 5;

function layout(index: number, perFloor: number) {
  const cols = Math.min(perFloor, ROW);
  const col = index % cols;
  const row = Math.floor(index / cols);
  const spanX = cols * (UNIT_WIDTH + GAP) - GAP;
  return {
    x: col * (UNIT_WIDTH + GAP) - spanX / 2 + UNIT_WIDTH / 2,
    z: row * (UNIT_DEPTH + GAP),
  };
}

function isPlaced(unit: UnitResponse): boolean {
  return (
    unit.x_coordinate != null &&
    unit.y_coordinate != null &&
    unit.z_coordinate != null &&
    unit.width_m != null &&
    unit.length_m != null &&
    unit.height_m != null
  );
}

export function BuildingScene({
  units,
  selectedId,
  onSelect,
  className,
}: {
  units: SceneUnit[];
  selectedId: string | null;
  onSelect: (unitId: string | null) => void;
  className?: string;
}) {
  const mountRef = React.useRef<HTMLDivElement>(null);
  const selectRef = React.useRef(onSelect);
  selectRef.current = onSelect;

  // Imperative handles the React tree must not own, but effects must reach.
  const meshesRef = React.useRef<Map<string, THREE.Mesh>>(new Map());
  const baseColour = React.useRef<Map<string, number>>(new Map());
  const [hovered, setHovered] = React.useState<string | null>(null);

  React.useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xeef2f7);
    scene.fog = new THREE.Fog(0xeef2f7, 60, 220);

    const camera = new THREE.PerspectiveCamera(
      45,
      mount.clientWidth / Math.max(1, mount.clientHeight),
      0.1,
      1000,
    );

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    mount.appendChild(renderer.domElement);

    // Zoom, rotate and pan all come from OrbitControls; the polar clamp stops
    // the camera dropping under the ground plane, which reads as a glitch.
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.maxPolarAngle = Math.PI / 2 - 0.05;
    controls.minDistance = 8;
    controls.maxDistance = 400;

    scene.add(new THREE.HemisphereLight(0xffffff, 0xb8c4d4, 1.05));
    const sun = new THREE.DirectionalLight(0xffffff, 1.35);
    sun.position.set(40, 70, 30);
    sun.castShadow = true;
    sun.shadow.mapSize.set(1024, 1024);
    sun.shadow.camera.near = 1;
    sun.shadow.camera.far = 300;
    const s = 80;
    sun.shadow.camera.left = -s;
    sun.shadow.camera.right = s;
    sun.shadow.camera.top = s;
    sun.shadow.camera.bottom = -s;
    scene.add(sun);
    scene.add(new THREE.AmbientLight(0xffffff, 0.25));

    /* ---------------------------------------------------- build the tower --- */

    const byFloor = new Map<number, SceneUnit[]>();
    for (const u of units) {
      const list = byFloor.get(u.floor.floor_number) ?? [];
      list.push(u);
      byFloor.set(u.floor.floor_number, list);
    }
    const floorNumbers = [...byFloor.keys()].sort((a, b) => a - b);
    const lowest = floorNumbers[0] ?? 0;
    const widest = Math.max(1, ...[...byFloor.values()].map((l) => Math.min(l.length, ROW)));
    const deepest = Math.max(
      1,
      ...[...byFloor.values()].map((l) => Math.ceil(l.length / ROW)),
    );
    let plateX = widest * (UNIT_WIDTH + GAP) + 2;
    let plateZ = deepest * (UNIT_DEPTH + GAP) + 2;

    // Measured extent of the units that carry real coordinates. Seeded inverted
    // so the first placed unit defines the box; if none does, the comparison
    // below stays false and the synthetic plate is used unchanged.
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;

    const group = new THREE.Group();
    const meshes = meshesRef.current;
    meshes.clear();
    baseColour.current.clear();

    const unitGeo = new THREE.BoxGeometry(UNIT_WIDTH, FLOOR_HEIGHT - SLAB - 0.25, UNIT_DEPTH);
    const edgeGeo = new THREE.EdgesGeometry(unitGeo);
    const edgeMat = new THREE.LineBasicMaterial({ color: 0x1f2937, transparent: true, opacity: 0.28 });

    for (const floorNumber of floorNumbers) {
      const list = byFloor.get(floorNumber)!;
      // Basements sit below grade, so the index is relative to the lowest floor
      // present rather than to zero.
      const y = (floorNumber - lowest) * FLOOR_HEIGHT;

      const slab = new THREE.Mesh(
        new THREE.BoxGeometry(plateX, SLAB, plateZ),
        new THREE.MeshStandardMaterial({ color: 0xd7dee8, roughness: 0.9, metalness: 0 }),
      );
      slab.position.set(0, y, 0);
      slab.receiveShadow = true;
      group.add(slab);

      list.forEach((entry, i) => {
        const tone = statusTone(entry.unit.verification_outcome, entry.openAlerts);
        const colour = TONE_HEX[tone];

        // Placed units are drawn at their recorded position and size. The scene
        // and the register must agree on where a unit is, or clicking a block
        // opens a record describing a different flat.
        const placed = isPlaced(entry.unit);
        const w = placed ? entry.unit.width_m! : UNIT_WIDTH;
        const d = placed ? entry.unit.length_m! : UNIT_DEPTH;
        const h = placed ? entry.unit.height_m! : FLOOR_HEIGHT - SLAB - 0.25;

        const geo = placed ? new THREE.BoxGeometry(w, h, d) : unitGeo;
        const mesh = new THREE.Mesh(
          geo,
          new THREE.MeshStandardMaterial({
            color: colour,
            roughness: 0.55,
            metalness: 0.05,
            transparent: true,
            opacity: 0.94,
          }),
        );

        if (placed) {
          // Local frame: x east, y north, z up, origin at the building footprint
          // centroid. Three.js is y-up and z-toward-viewer, so the register's y
          // becomes the scene's z and the register's z becomes the scene's y.
          // The half-extent offsets move the box's origin from its corner to its
          // centre, which is the point Three.js positions from.
          mesh.position.set(
            entry.unit.x_coordinate! + w / 2,
            entry.unit.z_coordinate! + h / 2,
            entry.unit.y_coordinate! + d / 2,
          );
        } else {
          const { x, z } = layout(i, list.length);
          mesh.position.set(x, y + h / 2 + SLAB / 2, z - (deepest - 1) * (UNIT_DEPTH + GAP) / 2);
        }

        mesh.castShadow = true;
        mesh.receiveShadow = true;
        mesh.userData.unitId = entry.unit.unit_id;
        group.add(mesh);

        // Grow the measured extent from whichever units are real. A building
        // whose units are all placed has its plate and camera sized from the
        // register; one that is not falls back to the synthetic span.
        if (placed) {
          minX = Math.min(minX, entry.unit.x_coordinate!);
          maxX = Math.max(maxX, entry.unit.x_coordinate! + w);
          minY = Math.min(minY, entry.unit.y_coordinate!);
          maxY = Math.max(maxY, entry.unit.y_coordinate! + d);
        }

        const edges = new THREE.LineSegments(
          placed ? new THREE.EdgesGeometry(geo) : edgeGeo,
          edgeMat,
        );
        edges.position.copy(mesh.position);
        group.add(edges);

        meshes.set(entry.unit.unit_id, mesh);
        baseColour.current.set(entry.unit.unit_id, colour);
      });
    }

    // Real coordinates win where any exist; otherwise the synthetic plate stands.
    if (maxX > minX && maxY > minY) {
      const padX = maxX - minX + 2;
      const padZ = maxY - minY + 2;
      // The scene is framed around the origin, so a building whose units sit
      // entirely to one side would open half off-screen: shift the tower so its
      // own centre is the origin.
      group.position.x = -(minX + maxX) / 2;
      group.position.z = -(minY + maxY) / 2;
      plateX = Math.max(plateX, padX);
      plateZ = Math.max(plateZ, padZ);
    }

    const height = (floorNumbers.length || 1) * FLOOR_HEIGHT;

    const ground = new THREE.Mesh(
      new THREE.CircleGeometry(Math.max(plateX, plateZ) * 2.2, 64),
      new THREE.MeshStandardMaterial({ color: 0xdfe6ee, roughness: 1 }),
    );
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -0.02;
    ground.receiveShadow = true;
    scene.add(ground);

    const grid = new THREE.GridHelper(Math.max(plateX, plateZ) * 4, 40, 0xc3ccd8, 0xd6dde6);
    grid.position.y = -0.01;
    scene.add(grid);

    group.position.y = 0;
    scene.add(group);

    // Frame the whole tower on first paint: a viewer that opens inside a wall
    // is indistinguishable from one that failed to load.
    const radius = Math.max(plateX, plateZ, height) * 0.9;
    camera.position.set(radius * 0.9, height * 0.72 + 8, radius * 1.25);
    controls.target.set(0, height / 2, 0);
    controls.update();

    /* -------------------------------------------------------- interaction --- */

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let hoverId: string | null = null;
    let downAt = { x: 0, y: 0 };

    const pick = (event: PointerEvent): string | null => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObjects([...meshes.values()], false);
      return (hits[0]?.object.userData.unitId as string | undefined) ?? null;
    };

    const onPointerMove = (event: PointerEvent) => {
      const id = pick(event);
      if (id !== hoverId) {
        hoverId = id;
        setHovered(id);
        renderer.domElement.style.cursor = id ? "pointer" : "grab";
      }
    };

    const onPointerDown = (event: PointerEvent) => {
      downAt = { x: event.clientX, y: event.clientY };
    };

    const onPointerUp = (event: PointerEvent) => {
      // A drag is a camera orbit, not a selection. Without this every rotation
      // ending over a flat would also select it.
      const moved =
        Math.abs(event.clientX - downAt.x) > 4 || Math.abs(event.clientY - downAt.y) > 4;
      if (moved) return;
      selectRef.current(pick(event));
    };

    renderer.domElement.style.cursor = "grab";
    renderer.domElement.addEventListener("pointermove", onPointerMove);
    renderer.domElement.addEventListener("pointerdown", onPointerDown);
    renderer.domElement.addEventListener("pointerup", onPointerUp);

    /* ------------------------------------------------------------- render --- */

    let frame = 0;
    const tick = () => {
      controls.update();
      renderer.render(scene, camera);
      frame = requestAnimationFrame(tick);
    };
    tick();

    const observer = new ResizeObserver(() => {
      const w = mount.clientWidth;
      const h = Math.max(1, mount.clientHeight);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    });
    observer.observe(mount);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      renderer.domElement.removeEventListener("pointermove", onPointerMove);
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      renderer.domElement.removeEventListener("pointerup", onPointerUp);
      controls.dispose();
      // Dispose explicitly: WebGL buffers are not reachable by the GC through
      // the JS objects alone, and this component remounts on every building.
      scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh || obj instanceof THREE.LineSegments) {
          obj.geometry.dispose();
          const m = obj.material;
          if (Array.isArray(m)) m.forEach((x) => x.dispose());
          else m.dispose();
        }
      });
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement);
      meshes.clear();
    };
  }, [units]);

  // Selection and hover are material tweaks on existing meshes, so they never
  // rebuild the scene.
  React.useEffect(() => {
    for (const [id, mesh] of meshesRef.current) {
      const material = mesh.material as THREE.MeshStandardMaterial;
      const base = baseColour.current.get(id) ?? 0x94a3b8;
      const isSelected = id === selectedId;
      const isHovered = id === hovered;
      material.color.setHex(base);
      if (isSelected) material.color.offsetHSL(0, 0.08, 0.12);
      else if (isHovered) material.color.offsetHSL(0, 0.04, 0.06);
      material.emissive = new THREE.Color(isSelected ? base : 0x000000);
      material.emissiveIntensity = isSelected ? 0.35 : 0;
      material.opacity = selectedId && !isSelected ? 0.55 : 0.94;
      mesh.scale.setScalar(isSelected ? 1.04 : 1);
    }
  }, [selectedId, hovered, units]);

  return <div ref={mountRef} className={className} role="img" aria-label="3D model of the building" />;
}
