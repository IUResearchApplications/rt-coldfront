// SPDX-FileCopyrightText: (C) ColdFront Authors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { renderChart, ColorPalette } from './data';
import type { ChartData, ChartDataItem } from './data';
import { createDoughnutChart } from './doughnutChartFactory';

export function initProjectUserChart(): void {
  renderChart('project-user-summary-chart', createProjectUserChart);
}

function createProjectUserChart(
  canvas: HTMLCanvasElement,
  chartData: ChartData
): void {
  createDoughnutChart(canvas, {
    title: 'Project Users',
    labels: chartData.data.map((row: ChartDataItem) => row.name),
    values: chartData.data.map((row: ChartDataItem) => row.total),
    backgroundColors: ColorPalette.PRIMARY,
    radius: '70%',
  });
}
