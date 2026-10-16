import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

@immutable
class ReadingPreferences {
  static const minTextScale = .85;
  static const maxTextScale = 1.5;
  static const minSidebarWidth = 300.0;
  static const maxSidebarWidth = 520.0;
  final double textScale;
  final double sidebarWidth;
  const ReadingPreferences({this.textScale = 1, this.sidebarWidth = 366});

  ReadingPreferences copyWith({double? textScale, double? sidebarWidth}) =>
      ReadingPreferences(
        textScale: _bounded(
            textScale ?? this.textScale, minTextScale, maxTextScale, 1),
        sidebarWidth: _bounded(sidebarWidth ?? this.sidebarWidth,
            minSidebarWidth, maxSidebarWidth, 366),
      );

  /// Keep room for a readable article and its padding when a window shrinks.
  /// This never changes the user's saved preference.
  double sidebarWidthFor(double layoutWidth) => sidebarWidth.clamp(
        minSidebarWidth,
        math.max(
            minSidebarWidth, math.min(maxSidebarWidth, layoutWidth - 620 - 48)),
      );

  static double _bounded(
          double value, double min, double max, double fallback) =>
      value.isFinite ? value.clamp(min, max) : fallback;
}

/// Local display preferences only; contains no account or reading data.
class ReadingPreferencesStore extends ChangeNotifier {
  static const storageKey = 'optinfo_reading_preferences_v1';
  static final shared = ReadingPreferencesStore();
  final Future<SharedPreferences> Function() _preferences;
  ReadingPreferences _value = const ReadingPreferences();
  ReadingPreferences get value => _value;
  bool saveFailed = false;
  int _revision = 0;
  Future<void>? _loading, _writing;

  ReadingPreferencesStore({Future<SharedPreferences> Function()? preferences})
      : _preferences = preferences ?? SharedPreferences.getInstance;

  Future<void> load() => _loading ??= _restore();

  Future<void> _restore() async {
    final revision = _revision;
    try {
      final encoded = (await _preferences()).getString(storageKey);
      if (encoded == null) return;
      final data = jsonDecode(encoded);
      if (data is! Map || revision != _revision) return;
      _value = const ReadingPreferences().copyWith(
        textScale: (data['text_scale'] as num?)?.toDouble(),
        sidebarWidth: (data['sidebar_width'] as num?)?.toDouble(),
      );
      notifyListeners();
    } catch (_) {
      // Unavailable or older local preferences must never block reading.
    }
  }

  Future<void> update({double? textScale, double? sidebarWidth}) {
    _value = _value.copyWith(textScale: textScale, sidebarWidth: sidebarWidth);
    _revision++;
    notifyListeners();
    return _writing ??= _persist().whenComplete(() {
      _writing = null;
    });
  }

  Future<void> reset() => update(textScale: 1, sidebarWidth: 366);

  Future<void> _persist() async {
    while (true) {
      final revision = _revision;
      final encoded = jsonEncode({
        'text_scale': _value.textScale,
        'sidebar_width': _value.sidebarWidth,
      });
      var failed = false;
      try {
        failed = !await (await _preferences()).setString(storageKey, encoded);
      } catch (_) {
        failed = true;
      }
      if (saveFailed != failed) {
        saveFailed = failed;
        notifyListeners();
      }
      if (revision == _revision) return;
    }
  }
}

/// Preserve the OS accessibility scaling curve, then apply the reading choice.
class ReadingTextScaler extends TextScaler {
  final TextScaler system;
  final double factor;
  const ReadingTextScaler(this.system, this.factor);
  @override
  double scale(double fontSize) => system.scale(fontSize) * factor;
  @override
  double get textScaleFactor => scale(14) / 14;
  @override
  bool operator ==(Object other) =>
      other is ReadingTextScaler &&
      other.system == system &&
      other.factor == factor;
  @override
  int get hashCode => Object.hash(system, factor);
}

class ReadingPreferencesView extends StatelessWidget {
  final ReadingPreferencesStore preferences;
  final Widget child;
  const ReadingPreferencesView(
      {super.key, required this.preferences, required this.child});

  static ReadingPreferencesStore of(BuildContext context) =>
      context
          .getInheritedWidgetOfExactType<_ReadingPreferencesScope>()
          ?.preferences ??
      ReadingPreferencesStore.shared;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: preferences,
        builder: (context, _) => _ReadingPreferencesScope(
          preferences: preferences,
          child: MediaQuery(
            data: MediaQuery.of(context).copyWith(
              textScaler: ReadingTextScaler(MediaQuery.textScalerOf(context),
                  preferences.value.textScale),
            ),
            child: child,
          ),
        ),
      );
}

class _ReadingPreferencesScope extends InheritedWidget {
  final ReadingPreferencesStore preferences;
  const _ReadingPreferencesScope(
      {required this.preferences, required super.child});
  @override
  bool updateShouldNotify(_ReadingPreferencesScope oldWidget) =>
      oldWidget.preferences != preferences;
}

class ReadingSettingsDialog extends StatelessWidget {
  final ReadingPreferencesStore preferences;
  const ReadingSettingsDialog({super.key, required this.preferences});

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: preferences,
        builder: (context, _) {
          final value = preferences.value;
          final wide = MediaQuery.sizeOf(context).width >= 1100;
          final layoutWidth =
              math.min(1250.0, MediaQuery.sizeOf(context).width);
          final actualWidth = value.sidebarWidthFor(layoutWidth);
          return AlertDialog(
            title: const Text('阅读设置'),
            content: SizedBox(
              width: 440,
              child: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('文字大小',
                        style: TextStyle(fontWeight: FontWeight.w600)),
                    const SizedBox(height: 6),
                    Text('${(value.textScale * 100).round()}% · 在系统字号基础上调整',
                        style: const TextStyle(fontSize: 12)),
                    Slider(
                      key: const ValueKey('reading-font-slider'),
                      value: value.textScale,
                      min: ReadingPreferences.minTextScale,
                      max: ReadingPreferences.maxTextScale,
                      divisions: 13,
                      label: '${(value.textScale * 100).round()}%',
                      semanticFormatterCallback: (v) => '${(v * 100).round()}%',
                      onChanged: (v) => preferences.update(textScale: v),
                    ),
                    const Text('资讯列表、正文、图注与探究回答同步调整。',
                        style: TextStyle(fontSize: 12, color: Colors.black54)),
                    const SizedBox(height: 24),
                    const Text('探究与词条侧栏',
                        style: TextStyle(fontWeight: FontWeight.w600)),
                    const SizedBox(height: 6),
                    Text('${value.sidebarWidth.round()} px',
                        style: const TextStyle(fontSize: 12)),
                    Slider(
                      key: const ValueKey('reading-sidebar-slider'),
                      value: value.sidebarWidth,
                      min: ReadingPreferences.minSidebarWidth,
                      max: ReadingPreferences.maxSidebarWidth,
                      divisions: 22,
                      label: '${value.sidebarWidth.round()} px',
                      semanticFormatterCallback: (v) => '${v.round()} 像素',
                      onChanged: wide
                          ? (v) => preferences.update(sidebarWidth: v)
                          : null,
                    ),
                    Text(
                      !wide
                          ? '在宽窗口中可调整侧栏；窄屏继续使用底部探究入口。'
                          : actualWidth < value.sidebarWidth
                              ? '当前窗口显示为 ${actualWidth.round()} px，扩大窗口后恢复设定宽度。'
                              : '窗口缩小时自动收窄，为正文保留阅读空间。',
                      style:
                          const TextStyle(fontSize: 12, color: Colors.black54),
                    ),
                    const SizedBox(height: 16),
                    Text(
                        preferences.saveFailed
                            ? '本次设置暂未保存到设备，可重试调整。'
                            : '调整立即生效，自动保存在这台设备。',
                        style: const TextStyle(
                            fontSize: 11, color: Colors.black54)),
                  ],
                ),
              ),
            ),
            actions: [
              TextButton(
                  onPressed: preferences.reset, child: const Text('恢复默认')),
              FilledButton(
                  onPressed: () => Navigator.pop(context),
                  child: const Text('完成')),
            ],
          );
        },
      );
}
