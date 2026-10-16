import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:optinfo/reading_preferences.dart';
import 'package:optinfo/screens.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'reading_preferences_test.dart' show ReadingApi, mountReading;

class CaptureReadingApi extends ReadingApi {
  final Uint8List png;
  CaptureReadingApi(this.png);
  @override
  Future<Uint8List> figureImage(String url) async => png;
}

Future<void> captureReading(WidgetTester tester, String name) async {
  if (Platform.environment['OPTINFO_CAPTURE_LAYOUT'] != '1') return;
  final boundary = tester.renderObject<RenderRepaintBoundary>(
      find.byKey(const ValueKey('reading-preferences-capture')));
  await tester.runAsync(() async {
    final image = await boundary.toImage();
    final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
    final file = File('../.ops/$name.png');
    await file.parent.create(recursive: true);
    await file.writeAsBytes(bytes!.buffer.asUint8List());
    image.dispose();
  });
}

Future<Uint8List> figureFixture() async {
  final recorder = ui.PictureRecorder();
  final canvas = Canvas(recorder);
  canvas.drawColor(const Color(0xFFF1F5EF), BlendMode.src);
  final grid = Paint()
    ..color = const Color(0xFFCCD9D3)
    ..strokeWidth = 1;
  for (var i = 0; i < 6; i++) {
    canvas.drawLine(
        Offset(40, 30.0 + i * 38), Offset(600, 30.0 + i * 38), grid);
  }
  final line = Path()..moveTo(40, 190);
  for (var x = 0; x <= 560; x++) {
    line.lineTo(40.0 + x, 175 - 110 * math.exp(-math.pow((x - 280) / 100, 2)));
  }
  canvas.drawPath(
      line,
      Paint()
        ..color = const Color(0xFF257568)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 5);
  final picture = recorder.endRecording();
  final image = await picture.toImage(640, 240);
  final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
  image.dispose();
  picture.dispose();
  return bytes!.buffer.asUint8List();
}

void main() {
  testWidgets(
      'image and mathematical text stay readable at both sidebar limits',
      (tester) async {
    SharedPreferences.setMockInitialValues({});
    tester.view.physicalSize = const Size(1200, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    if (Platform.environment['OPTINFO_CAPTURE_LAYOUT'] == '1') {
      final manifest =
          jsonDecode(await rootBundle.loadString('FontManifest.json')) as List;
      for (final group in manifest) {
        final loader = FontLoader(group['family'] as String);
        for (final font in group['fonts'] as List) {
          loader.addFont(rootBundle.load(font['asset'] as String));
        }
        await loader.load();
      }
    }
    final png = (await tester.runAsync(figureFixture))!;
    final api = CaptureReadingApi(png);
    api.article.raw['content_revision'] = 2;
    api.article.raw['analysis_status'] = 'complete';
    api.article.raw['content'] = {
      'results': r'这是用于布局验证的示意数据。场强关系为 \(I \propto |E|^2\)，积分形式为：'
          r'\[\eta=\frac{\int_0^T P_{out}(t)\,dt}{\int_0^T P_{in}(t)\,dt}\]',
    };
    api.article.raw['figures'] = [
      {
        'image_path': '/api/media/${'a' * 64}.png',
        'caption': r'图 1｜用于界面验证的示意曲线，展示 \(I(\lambda)\) 的变化；不是实际研究结果。',
      },
    ];
    final preferences = ReadingPreferencesStore();
    await preferences.update(textScale: 1.5, sidebarWidth: 300);
    await mountReading(tester, api, preferences);
    await tester.runAsync(() => precacheImage(
        MemoryImage(png), tester.element(find.byType(FigureView))));
    await tester.pumpAndSettle();
    final figure = find.byType(FigureView);
    expect(find.byType(Image), findsOneWidget);
    expect(find.text('暂无可展示的图片'), findsNothing);
    await Scrollable.ensureVisible(tester.element(figure), alignment: .7);
    await tester.pumpAndSettle();
    expect(
        tester
            .getSize(find.byKey(const ValueKey('article-explore-panel')))
            .width,
        300);
    expect(tester.takeException(), isNull);
    await captureReading(tester, 'reading-sidebar-300-font150');

    await preferences.update(sidebarWidth: 520);
    await tester.pumpAndSettle();
    await Scrollable.ensureVisible(tester.element(figure), alignment: .7);
    await tester.pumpAndSettle();
    expect(
        tester
            .getSize(find.byKey(const ValueKey('article-explore-panel')))
            .width,
        520);
    expect(tester.takeException(), isNull);
    await captureReading(tester, 'reading-sidebar-520-font150');

    tester.view.physicalSize = const Size(1100, 1000);
    await tester.pumpAndSettle();
    expect(
        tester
            .getSize(find.byKey(const ValueKey('article-explore-panel')))
            .width,
        432);
    expect(tester.takeException(), isNull);
    await captureReading(tester, 'reading-sidebar-clamped-font150');
    await tester.tap(find.byTooltip('阅读设置'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('reading-font-slider')), findsOneWidget);
    expect(tester.takeException(), isNull);
    await captureReading(tester, 'reading-settings-font150-sidebar520');
  });
}
