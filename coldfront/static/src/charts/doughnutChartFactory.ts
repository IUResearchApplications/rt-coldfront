// SPDX-FileCopyrightText: (C) ColdFront Authors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import Chart from 'chart.js/auto';
import type { LegendItem } from 'chart.js/auto';

interface DoughnutArcElement {
  innerRadius: number;
  outerRadius: number;
  startAngle: number;
  endAngle: number;
}

interface DoughnutChartOptions {
  radius: string | number;
  cutout: string | number;
}

export interface DoughnutChartConfig {
  title?: string;
  labels: string[];
  values: number[];
  backgroundColors: string[];
  cutout?: string | number;
  radius?: string | number;
  borderColor?: string;
  borderWidth?: number;
}

const centerTextPlugin = {
  id: 'centerText',
  beforeDraw(chart: Chart): void {
    const configType = (chart.config as unknown as { type: string }).type;
    if (configType !== 'doughnut') return;

    const { ctx, chartArea } = chart;
    const centerX = chartArea.left + chartArea.width / 2;
    const centerY = chartArea.top + chartArea.height / 2;

    const chartOptions = chart.options as unknown as DoughnutChartOptions;
    const cutout = chartOptions.cutout || '50%';
    let cutoutRatio: number;
    if (typeof cutout === 'string') {
      cutoutRatio = parseFloat(cutout) / 100;
    } else {
      cutoutRatio = cutout;
    }
    const radius = Math.min(chartArea.width, chartArea.height) / 2;
    const innerRadius = radius * cutoutRatio;

    const text = (chart.config.options as unknown as { centerText?: string })
      .centerText;
    if (!text) return;
    let fontSize = Math.min(18, Math.floor(innerRadius * 0.35));
    const maxWidth = innerRadius * 1.2;

    ctx.save();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#333';
    ctx.font = `bold ${fontSize}px sans-serif`;

    const measuredWidth = ctx.measureText(text).width;
    if (measuredWidth > maxWidth) {
      fontSize = Math.max(
        12,
        Math.floor(fontSize * (maxWidth / measuredWidth))
      );
      ctx.font = `bold ${fontSize}px sans-serif`;
    }

    ctx.fillText(text, centerX, centerY);
    ctx.restore();
  },
};

const percentagePlugin = {
  id: 'percentageLabels',
  afterDraw(chart: Chart): void {
    const configType = (chart.config as unknown as { type: string }).type;
    if (configType !== 'doughnut') return;

    const { ctx, chartArea } = chart;
    const meta = chart.getDatasetMeta(0);
    const values = chart.data.datasets[0].data as number[];
    const visibleTotal = values.reduce(
      (sum, val, i) => (chart.getDataVisibility(i) ? sum + val : sum),
      0
    );

    const firstArc = meta.data[0] as unknown as DoughnutArcElement;
    const innerRadius = firstArc.innerRadius;
    const outerRadius = firstArc.outerRadius;
    const midRadius = (innerRadius + outerRadius) / 2;
    const cx = chartArea.left + chartArea.width / 2;
    const cy = chartArea.top + chartArea.height / 2;

    ctx.save();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 13px sans-serif';

    for (let i = 0; i < meta.data.length; i++) {
      if (!chart.getDataVisibility(i)) continue;
      if (values[i] === 0) continue;

      const arc = meta.data[i] as unknown as DoughnutArcElement;
      const midAngle = (arc.startAngle + arc.endAngle) / 2;
      const x = cx + Math.cos(midAngle) * midRadius;
      const y = cy + Math.sin(midAngle) * midRadius;

      const percentage = ((values[i] / visibleTotal) * 100).toFixed(1) + '%';
      ctx.fillText(percentage, x, y);
    }

    ctx.restore();
  },
};

function generateLegendLabels(chart: Chart): LegendItem[] {
  const dataset = chart.data.datasets[0];
  const values = dataset.data as number[];
  const bgColors = dataset.backgroundColor as string[];
  return chart.data.labels!.map((label, i) => ({
    text: `${label} (${values[i]})`,
    fillStyle: bgColors[i],
    hidden: !chart.getDataVisibility(i),
    index: i,
  }));
}

export function createDoughnutChart(
  canvas: HTMLCanvasElement,
  config: DoughnutChartConfig
): Chart {
  const chart = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels: config.labels,
      datasets: [
        {
          data: config.values,
          backgroundColor: config.backgroundColors,
          borderColor: config.borderColor
            ? config.backgroundColors.map(() => config.borderColor!)
            : config.backgroundColors.map((color) => color + '80'),
          borderWidth: config.borderWidth ?? 1,
        },
      ],
    },
    options: {
      radius: config.radius ?? '80%',
      cutout: config.cutout ?? '65%',
      responsive: true,
      maintainAspectRatio: false,
      devicePixelRatio: window.devicePixelRatio || 1,
      centerText: config.title ?? '',
      plugins: {
        tooltip: {
          enabled: false,
        },
        legend: {
          position: 'bottom',
          labels: {
            generateLabels: generateLegendLabels,
          },
        },
        title: {
          display: false,
        },
      },
    } as unknown as Record<string, unknown>,
    plugins: [centerTextPlugin, percentagePlugin],
  });

  return chart;
}
