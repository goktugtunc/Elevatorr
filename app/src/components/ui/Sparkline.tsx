import { useId, useMemo, useState } from 'react';
import { StyleSheet, View, type LayoutChangeEvent, type ViewStyle } from 'react-native';
import Svg, { Defs, LinearGradient, Path, Stop } from 'react-native-svg';

import { colors, pnlColor } from '@/theme';

/**
 * Sparkline (FE-20) — Panel, Keşfet kartı ve Trader Profili'ndeki mini performans grafiği.
 * Eksen/ızgara yok: yalnızca eğilim. Renk verilmezse ilk→son yönüne göre kâr/zarar rengi.
 * Genişlik verilmezse kapsayıcıdan ölçülür (kart içinde esnek kullanım).
 */
export interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
  strokeWidth?: number;
  /** Çizginin altına yumuşak dolgu ekler. */
  fill?: boolean;
  style?: ViewStyle;
}

export function Sparkline({
  data,
  width,
  height = 40,
  color,
  strokeWidth = 2,
  fill = true,
  style,
}: SparklineProps) {
  const [measured, setMeasured] = useState(0);
  // Aynı ekranda birden fazla sparkline olabilir → gradient id'si benzersiz olmalı.
  const gradientId = `spark-${useId().replace(/:/g, '')}`;
  const w = width ?? measured;

  const onLayout = (e: LayoutChangeEvent) => {
    if (width === undefined) setMeasured(e.nativeEvent.layout.width);
  };

  const stroke =
    color ?? (data.length > 1 ? pnlColor(data[data.length - 1] - data[0]) : colors.text3);

  const paths = useMemo(
    () => buildPaths(data, w, height, strokeWidth),
    [data, w, height, strokeWidth],
  );

  return (
    <View onLayout={onLayout} style={[{ height }, styles.wrap, style]}>
      {paths ? (
        <Svg width={w} height={height}>
          {fill ? (
            <Defs>
              <LinearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <Stop offset="0" stopColor={stroke} stopOpacity={0.18} />
                <Stop offset="1" stopColor={stroke} stopOpacity={0} />
              </LinearGradient>
            </Defs>
          ) : null}
          {fill ? <Path d={paths.area} fill={`url(#${gradientId})`} /> : null}
          <Path
            d={paths.line}
            stroke={stroke}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeLinejoin="round"
            fill="none"
          />
        </Svg>
      ) : null}
    </View>
  );
}

function buildPaths(
  data: number[],
  width: number,
  height: number,
  strokeWidth: number,
): { line: string; area: string } | null {
  if (width <= 0 || data.length < 2) return null;

  const pad = strokeWidth; // çizgi kalınlığı kırpılmasın
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const stepX = width / (data.length - 1);
  const usableH = Math.max(height - pad * 2, 1);

  const points = data.map((value, i) => {
    const x = i * stepX;
    const y = pad + (1 - (value - min) / span) * usableH;
    return [x, y] as const;
  });

  const line = points
    .map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(2)} ${y.toFixed(2)}`)
    .join(' ');
  const area = `${line} L${width.toFixed(2)} ${height} L0 ${height} Z`;
  return { line, area };
}

const styles = StyleSheet.create({
  wrap: { width: '100%', justifyContent: 'center' },
});
