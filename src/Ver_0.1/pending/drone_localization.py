import cv2
import numpy as np
import time
import matplotlib.pyplot as plt
import pandas as pd
from scipy.optimize import least_squares
from dataclasses import dataclass

# Configure logging
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

@dataclass
class FeatureMatcher:
    detector_name: str

    def detect_and_compute(self, image):
        """Detect keypoints and compute descriptors using the specified detector."""
        if self.detector_name == "sift":
            detector = cv2.SIFT_create()
        elif self.detector_name == "orb":
            detector = cv2.ORB_create()
        elif self.detector_name == "akaze":
            detector = cv2.AKAZE_create()
        elif self.detector_name == "brisk":
            detector = cv2.BRISK_create()
        else:
            raise ValueError(f"Unsupported detector: {self.detector_name}")
        
        keypoints, descriptors = detector.detectAndCompute(image, None)
        return keypoints, descriptors

    def match_features(self, desc1, desc2):
        """Match features using FLANN or BFMatcher."""
        if self.detector_name in ["sift", "akaze"]:
            # Use FLANN for floating-point descriptors
            FLANN_INDEX_KDTREE = 1
            index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
            search_params = dict(checks=50)
            matcher = cv2.FlannBasedMatcher(index_params, search_params)
        else:
            # Use BFMatcher for binary descriptors
            matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        
        matches = matcher.match(desc1, desc2)
        matches = sorted(matches, key=lambda x: x.distance)
        return matches

@dataclass
class HybridFeatureMatcher:
    def __init__(self):
        self.orb = cv2.ORB_create()
        self.sift = cv2.SIFT_create()
        self.bf_matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        self.flann_matcher = cv2.FlannBasedMatcher(
            dict(algorithm=1, trees=5), dict(checks=50)
        )

    def detect_and_compute(self, image):
        """Detect keypoints and compute descriptors using ORB and SIFT."""
        orb_kp, orb_desc = self.orb.detectAndCompute(image, None)
        sift_kp, sift_desc = self.sift.detectAndCompute(image, None)
        return orb_kp, orb_desc, sift_kp, sift_desc

    def match_features(self, desc1, desc2):
        """Match features using a combination of ORB and SIFT."""
        orb_matches = self.bf_matcher.match(desc1[1], desc2[1])
        orb_matches = sorted(orb_matches, key=lambda x: x.distance)[:100]

        sift_matches = self.flann_matcher.match(desc1[3], desc2[3])
        sift_matches = sorted(sift_matches, key=lambda x: x.distance)[:100]

        combined_matches = orb_matches + sift_matches
        return combined_matches

    def refine_homography(self, src_pts, dst_pts):
        """Refine homography using Levenberg-Marquardt optimization."""
        def residuals(H, src_pts, dst_pts):
            H = H.reshape(3, 3)
            projected = cv2.perspectiveTransform(src_pts, H)
            return (projected - dst_pts).ravel()

        H, _ = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
        result = least_squares(residuals, H.ravel(), args=(src_pts, dst_pts))
        return result.x.reshape(3, 3)

    def find_location(self, satellite_image, drone_image):
        """Find the drone's location relative to the satellite image."""
        orb_kp1, orb_desc1, sift_kp1, sift_desc1 = self.detect_and_compute(satellite_image)
        orb_kp2, orb_desc2, sift_kp2, sift_desc2 = self.detect_and_compute(drone_image)

        matches = self.match_features(
            (orb_kp1, orb_desc1, sift_kp1, sift_desc1),
            (orb_kp2, orb_desc2, sift_kp2, sift_desc2)
        )

        src_pts = np.float32([orb_kp1[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
        dst_pts = np.float32([orb_kp2[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)

        H = self.refine_homography(dst_pts, src_pts)

        h, w = drone_image.shape[:2]
        drone_center = np.array([[w / 2, h / 2]], dtype="float32").reshape(-1, 1, 2)
        satellite_position = cv2.perspectiveTransform(drone_center, H)

        return satellite_position[0][0], matches, orb_kp1, orb_kp2

@dataclass
class TestResult:
    detector_name: str
    computation_time: float
    matches_count: int
    position_error: float
    homography_accuracy: float

class DroneLocalizationEvaluator:
    def __init__(self, satellite_image_path: str, drone_image_path: str):
        self.satellite_image = cv2.imread(satellite_image_path, cv2.IMREAD_GRAYSCALE)
        self.drone_image = cv2.imread(drone_image_path, cv2.IMREAD_GRAYSCALE)
        self.detectors = ["sift", "orb", "akaze", "brisk", "hfm"]
        self.results = []

    def evaluate_detectors(self):
        """Evaluate all detectors and compute metrics."""
        for detector in self.detectors:
            start_time = time.time()
            if detector == "hfm":
                finder = HybridFeatureMatcher()
            else:
                finder = FeatureMatcher(detector)
            position, matches, kp1, kp2 = finder.find_location(self.satellite_image, self.drone_image)
            computation_time = time.time() - start_time

            matches_count = len(matches)
            position_error = np.linalg.norm(position - np.array([self.satellite_image.shape[1] / 2, self.satellite_image.shape[0] / 2]))
            homography_accuracy = matches_count / (len(kp1) + len(kp2))

            self.results.append(TestResult(
                detector_name=detector,
                computation_time=computation_time,
                matches_count=matches_count,
                position_error=position_error,
                homography_accuracy=homography_accuracy
            ))

            self.visualize_matches(detector, kp1, kp2, matches)

    def visualize_matches(self, detector: str, kp1, kp2, matches):
        """Visualize feature matches."""
        matched_image = cv2.drawMatches(
            self.satellite_image, kp1, self.drone_image, kp2, matches[:50], None,
            flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS,
            matchColor=(0, 255, 0),  # Green color for matches
            singlePointColor=(255, 0, 0)  # Blue color for keypoints
        )
        plt.figure(figsize=(15, 10))
        plt.title(f"Feature Matches ({detector.upper()})", fontsize=16)
        plt.imshow(matched_image, cmap="gray")
        plt.axis("off")
        plt.savefig(f"matches_{detector}.png", bbox_inches="tight", dpi=300)
        plt.close()

    def analyze_results(self):
        """Analyze and summarize the results."""
        df = pd.DataFrame([vars(r) for r in self.results])
        print("\nDetector Performance Summary:")
        print(df)

        # Plot results
        fig, axes = plt.subplots(2, 2, figsize=(15, 10))
        fig.suptitle("Detector Performance Comparison", fontsize=18)

        df.plot(kind="bar", x="detector_name", y="computation_time", ax=axes[0, 0], title="Computation Time (s)", color="skyblue")
        df.plot(kind="bar", x="detector_name", y="matches_count", ax=axes[0, 1], title="Number of Matches", color="lightgreen")
        df.plot(kind="bar", x="detector_name", y="position_error", ax=axes[1, 0], title="Position Error (pixels)", color="salmon")
        df.plot(kind="bar", x="detector_name", y="homography_accuracy", ax=axes[1, 1], title="Homography Accuracy", color="gold")

        plt.tight_layout()
        plt.savefig("detector_performance.png", bbox_inches="tight", dpi=300)

# Example usage
if __name__ == "__main__":
    evaluator = DroneLocalizationEvaluator(
        satellite_image_path="satellite_image.jpg",
        drone_image_path="drone_image.jpg"
    )
    evaluator.evaluate_detectors()
    evaluator.analyze_results()
