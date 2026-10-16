import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:optinfo/api.dart';
import 'package:optinfo/models.dart';
import 'package:optinfo/paper_figure_image.dart';
import 'package:optinfo/paper_media.dart';
import 'package:optinfo/screens.dart';
import 'package:optinfo/scientific_text.dart';

final png = base64Decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=');
final mediaPath = '/api/media/${'a' * 64}.png';

class FigureApi extends Api {
  int loads = 0;
  FigureApi() : super(ConnectionStore());
  @override
  Future<Uint8List> figureImage(String path) async {
    loads++;
    return png;
  }
}

void main() {
  testWidgets(
      'image preview zooms local bytes and closes without a new request',
      (tester) async {
    tester.view.physicalSize = const Size(320, 640);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = FigureApi();
    await tester.pumpWidget(MaterialApp(
      builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(textScaler: TextScaler.linear(2)),
        child: child!,
      ),
      home: Scaffold(
          body: SingleChildScrollView(
        child: PaperFigureImage(api: api, path: mediaPath),
      )),
    ));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('点击放大图片'));
    await tester.pumpAndSettle();
    expect(find.byType(InteractiveViewer), findsOneWidget);
    final viewer =
        tester.widget<InteractiveViewer>(find.byType(InteractiveViewer));
    await tester.tap(find.byTooltip('放大图片'));
    await tester.pumpAndSettle();
    expect(viewer.transformationController!.value.getMaxScaleOnAxis(), 1.5);
    await tester.tap(find.byTooltip('还原图片大小'));
    await tester.pumpAndSettle();
    expect(viewer.transformationController!.value.getMaxScaleOnAxis(), 1);
    await tester.tap(find.byTooltip('关闭图片预览'));
    await tester.pumpAndSettle();
    expect(find.byType(InteractiveViewer), findsNothing);
    expect(api.loads, 1);
    expect(tester.takeException(), isNull);
  });

  test('authenticated image stays on server and forbids redirects', () async {
    final client = MockClient((request) async {
      expect(request.url.toString(), 'https://private.example$mediaPath');
      expect(request.headers['Authorization'], 'Bearer test-token');
      expect(request.followRedirects, isFalse);
      return http.Response.bytes(png, 200,
          headers: {'content-type': 'image/png'});
    });
    expect(
        await fetchPaperImage(
            base: 'https://private.example',
            token: 'test-token',
            path: mediaPath,
            client: client),
        png);
  });

  test('untrusted source URLs and traversal never receive credentials',
      () async {
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return http.Response('', 200);
    });
    for (final path in [
      'https://publisher.example/x.png',
      '//publisher.example/x.png',
      '/api/media/../../x.png',
      '$mediaPath?next=https://other.example',
      '/api/media/short.png'
    ]) {
      await expectLater(
          fetchPaperImage(
              base: 'https://private.example',
              token: 'test-token',
              path: path,
              client: client),
          throwsFormatException);
    }
    expect(calls, 0);
  });

  test('redirect, nonimage, oversized and invalid PNG responses are rejected',
      () async {
    for (final response in [
      http.Response('', 302,
          headers: {'location': 'https://publisher.example/x.png'}),
      http.Response('<html>sign in</html>', 200,
          headers: {'content-type': 'text/html'}),
      http.Response('invalid', 200, headers: {'content-type': 'image/png'}),
      http.Response.bytes(Uint8List(paperImageLimit + 1), 200,
          headers: {'content-type': 'image/png'}),
    ]) {
      await expectLater(
          fetchPaperImage(
              base: 'https://private.example',
              token: 'test-token',
              path: mediaPath,
              client: MockClient((_) async => response)),
          throwsFormatException);
    }
  });

  testWidgets(
      'figure prefers authenticated media and exposes Chinese and original captions',
      (tester) async {
    final api = FigureApi();
    final Json figure = {
      'image_path': mediaPath,
      'url': 'https://publisher.example/paper.pdf#page=2',
      'caption': r'中文结果图：温度 \(T_e\)。',
      'original_caption': r'Temperature \(T_e\) dynamics.',
      'selection_reason': r'展示电子密度 \(n_e\) 的变化。',
      'source_url': 'https://publisher.example/paper',
    };
    Widget page(double scale) => MaterialApp(
            home: Scaffold(
                body: MediaQuery(
          data: MediaQueryData(textScaler: TextScaler.linear(scale)),
          child: SingleChildScrollView(
              child: FigureView(api: api, figure: figure)),
        )));
    await tester.pumpWidget(page(1));
    await tester.pumpAndSettle();
    expect(find.byType(PaperFigureImage), findsOneWidget);
    expect(find.byType(Image), findsOneWidget);
    expect(api.loads, 1);
    expect(find.byType(ScientificText), findsNWidgets(3));
    await tester.ensureVisible(find.text('原文图注'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('原文图注'));
    await tester.pumpAndSettle();
    expect(find.byType(ScientificText), findsNWidgets(4));
    await tester.pumpWidget(page(1.5));
    await tester.pumpAndSettle();
    expect(api.loads, 1,
        reason: 'Changing font scale must not refetch the PNG');
    expect(tester.takeException(), isNull);
  });
}
