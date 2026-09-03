// SPDX-FileCopyrightText: (C) ColdFront Authors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { renderChart, ColorPalette } from './data';
import type { ChartData, ChartDataItem } from './data';
import { createDoughnutChart } from './doughnutChartFactory';

export function initResourceChart(): void {
  renderChart('resource-summary-chart', createResourceChart);
}

function createResourceChart(
  canvas: HTMLCanvasElement,
  chartData: ChartData
): void {
  createDoughnutChart(canvas, {
    title: 'Active Resources',
    labels: chartData.data.map((row: ChartDataItem) => row.name),
    values: chartData.data.map((row: ChartDataItem) => row.total),
    backgroundColors: ColorPalette.PRIMARY,
    radius: '75%',
  });
}
