// SPDX-FileCopyrightText: (C) ColdFront Authors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

export type ChartDataItem = {
  name: string;
  total: number;
};

export interface ChartData {
  total: number;
  data: ChartDataItem[];
}

export class ColorPalette {
  // Custom color palettes can be defined here. This one is from colorbrewer2 qualitative
  static readonly PRIMARY = [
    '#1f78b4',
    '#a6cee3',
    '#b2df8a',
    '#33a02c',
    '#fb9a99',
    '#e31a1c',
    '#fdbf6f',
    '#ff7f00',
    '#cab2d6',
    '#6a3d9a',
    '#ffff99',
    '#b15928',
  ];
}

export function renderChart(
  id: string,
  chartFunc: (canvas: HTMLCanvasElement, data: ChartData) => void
): void {
  const canvas = document.getElementById(id) as HTMLCanvasElement;
  if (canvas !== null) {
    const url = canvas.getAttribute('data-url');
    if (url == null) {
      return;
    }
    fetchChartData(url)
      .then((data) => {
        chartFunc(canvas, data);
      })
      .catch((error: unknown) => {
        console.log('Error loading chart data: ' + error);
        const ctx = canvas.getContext('2d');
        if (ctx) {
          ctx.fillStyle = 'red';
          ctx.font = '16px Arial';
          ctx.fillText('Error loading chart data', 10, 15);
        }
      });
  }
}

export async function fetchChartData(url: string): Promise<ChartData> {
  try {
    const response = await fetch(url);

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const data: ChartData = await response.json();
    return data;
  } catch (error) {
    console.error('Error fetching data:', error);
    throw error;
  }
}

// Get colors from CSS variables
export function getCSSVariable(variableName: string): string {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(variableName)
    .trim();
}
