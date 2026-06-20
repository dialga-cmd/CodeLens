"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";

export default function DottedSurface() {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const container = containerRef.current;
    const width = container.clientWidth;
    const height = container.clientHeight;

    // Scene
    const scene = new THREE.Scene();

    // Camera
    const camera = new THREE.PerspectiveCamera(75, width / height, 0.1, 1000);
    camera.position.z = 50;
    camera.position.y = 20;
    camera.lookAt(0, 0, 0);

    // Renderer
    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(window.devicePixelRatio);
    container.appendChild(renderer.domElement);

    // Grid of dots
    const dotsCount = 40;
    const spacing = 4;
    const particles = new THREE.BufferGeometry();
    const positions = new Float32Array(dotsCount * dotsCount * 3);

    let k = 0;
    for (let i = 0; i < dotsCount; i++) {
      for (let j = 0; j < dotsCount; j++) {
        positions[k++] = (i - dotsCount / 2) * spacing;
        positions[k++] = 0;
        positions[k++] = (j - dotsCount / 2) * spacing;
      }
    }

    particles.setAttribute("position", new THREE.BufferAttribute(positions, 3));

    // Material
    const material = new THREE.PointsMaterial({
      color: 0x00ff41,
      size: 0.2,
      transparent: true,
      opacity: 0.4,
    });

    const points = new THREE.Points(particles, material);
    scene.add(points);

    // Animation
    let time = 0;
    const animate = () => {
      requestAnimationFrame(animate);
      time += 0.02;

      const positions = particles.attributes.position.array as Float32Array;
      let k = 0;
      for (let i = 0; i < dotsCount; i++) {
        for (let j = 0; j < dotsCount; j++) {
          const x = positions[k];
          const z = positions[k + 2];
          // Wave logic
          positions[k + 1] = Math.sin(x * 0.2 + time) * 2 + Math.cos(z * 0.2 + time) * 2;
          k += 3;
        }
      }
      particles.attributes.position.needsUpdate = true;

      renderer.render(scene, camera);
    };

    animate();

    // Resize handler
    const handleResize = () => {
      const w = container.clientWidth;
      const h = container.clientHeight;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };

    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      container.removeChild(renderer.domElement);
      renderer.dispose();
    };
  }, []);

  return <div ref={containerRef} className="absolute inset-0 pointer-events-none opacity-40" />;
}
